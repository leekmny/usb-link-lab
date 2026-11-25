"""Device-bound pairing records protected by current-user Windows DPAPI."""

import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile


class PairingError(ValueError):
    pass


# Direct device IDs are hashes of USB identities. The relay slot is local and
# independent of DHCP, the Pi's address and its Windows adapter.
RELAY_PEER = "ulab-relay-iphone"



def encode_pairing_qr(secret: bytes) -> str:
    """Encode the full provisioned key with a strict, non-link QR marker."""
    if len(secret) != 32:
        raise PairingError("invalid_secret")
    return "ULABQR1:" + base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class Pairing:
    device: str = field(repr=False)
    secret: bytes = field(repr=False)

    def __post_init__(self):
        if not self.device or len(self.device) > 256:
            raise PairingError("invalid_device")
        if len(self.secret) != 32 or self.secret in (bytes(32), bytes(range(32))):
            raise PairingError("invalid_secret")

    @property
    def binding(self) -> str:
        # The outbox stores this local-only identifier instead of the device ID.
        context = b"USBLinkLab/Windows/outbox\0" + self.device.encode()
        return hmac.new(self.secret, context, hashlib.sha256).hexdigest()


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise PairingError("dpapi_unavailable")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_Blob)
    encrypt = crypt.CryptProtectData
    encrypt.argtypes = [
        blob_pointer, wintypes.LPCWSTR, blob_pointer, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_pointer,
    ]
    encrypt.restype = wintypes.BOOL
    decrypt = crypt.CryptUnprotectData
    decrypt.argtypes = [
        blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_pointer,
    ]
    decrypt.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming = _Blob(len(data), source)
    outgoing = _Blob()
    operation = encrypt if protect else decrypt
    description = "USBLinkLab app pairing" if protect else None
    try:
        if not operation(
            ctypes.byref(incoming), description, None, None, None, 1, ctypes.byref(outgoing)
        ):
            raise PairingError("dpapi_failed")
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.memset(source, 0, len(source))
        if outgoing.data:
            if not protect:
                ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel.LocalFree(outgoing.data)


def atomic_save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            file.close()
            os.replace(temporary, path)
        finally:
            file.close()
            temporary.unlink(missing_ok=True)


class PairingStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, pairing: Pairing) -> None:
        record = {"device": pairing.device, "secret": pairing.secret.hex()}
        atomic_save(self._path_for(pairing.device), _dpapi(json.dumps(record).encode(), protect=True))

    def _path_for(self, device: str) -> Path:
        return self.path.with_name("relay-pairing.dpapi") if device == RELAY_PEER else self.path

    def load(self, device: str) -> Pairing | None:
        pairing = self._read(self._path_for(device))
        return pairing if pairing and pairing.device == device else None

    def direct(self) -> Pairing | None:
        """Read the original direct record for an explicitly selected saved-key reconnect."""
        return self._read(self.path)

    def _read(self, path: Path) -> Pairing | None:
        try:
            with path.open("rb") as file:
                data = file.read(16385)
        except FileNotFoundError:
            return None
        if not 1 <= len(data) <= 16384:
            raise PairingError("invalid_record")
        try:
            record = json.loads(_dpapi(data, protect=False))
            pairing = Pairing(record["device"], bytes.fromhex(record["secret"]))
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise PairingError("invalid_record") from exc
        return pairing
