"""Versioned six-digit enrollment over a selected USB stream."""

import asyncio
import ctypes
from functools import lru_cache
import hashlib
import hmac
import os
from pathlib import Path
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


MAGIC = b"ULABP001"
PAIRING_TIMEOUT = 20.0
CODE_TTL = 120
MAX_SHARE = 512
TAG_LENGTH = 32
_HEADER = struct.Struct(">8sBH")
_KEY_LABELS = (
    b"ulab-ecjpake/1/psk",
    b"ulab-ecjpake/1/client-confirm",
    b"ulab-ecjpake/1/server-confirm",
)


class PairingProtocolError(Exception):
    """The attempt failed and its stream must not be used."""


class PairingBackendError(PairingProtocolError):
    """The pinned native crypto bridge is unavailable."""


@lru_cache(maxsize=1)
def _native():
    path = Path(__file__).resolve().parent / "_native" / "ulab_pairing.dll"
    if os.name != "nt" or not path.is_file():
        raise PairingBackendError("Native pairing library unavailable")
    try:
        dll = ctypes.CDLL(str(path), winmode=0x00000900)
        pointer = ctypes.c_void_p
        length = ctypes.c_size_t
        dll.ulab_pake_new.argtypes = [ctypes.c_int, pointer, length]
        dll.ulab_pake_new.restype = pointer
        dll.ulab_pake_write.argtypes = [pointer, ctypes.c_int, pointer, length, ctypes.POINTER(length)]
        dll.ulab_pake_write.restype = ctypes.c_int
        dll.ulab_pake_read.argtypes = [pointer, ctypes.c_int, pointer, length]
        dll.ulab_pake_read.restype = ctypes.c_int
        dll.ulab_pake_derive.argtypes = [pointer, pointer]
        dll.ulab_pake_derive.restype = ctypes.c_int
        dll.ulab_pake_free.argtypes = [pointer]
        dll.ulab_pake_free.restype = None
    except (OSError, AttributeError) as error:
        raise PairingBackendError("Native pairing library unavailable") from error
    return dll


def _check_code(code):
    if len(code) != 6 or any(digit not in "0123456789" for digit in code):
        raise PairingProtocolError("Pairing code must contain exactly six ASCII digits")


class _Exchange:
    def __init__(self, code, *, client):
        _check_code(code)
        self._dll = _native()
        self._context = self._dll.ulab_pake_new(0 if client else 1, code.encode("ascii"), 6)
        if not self._context:
            raise PairingProtocolError("Could not initialize EC J-PAKE")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._dll.ulab_pake_free(self._context)
        self._context = None

    def write(self, round_number):
        output = ctypes.create_string_buffer(MAX_SHARE)
        written = ctypes.c_size_t()
        if self._dll.ulab_pake_write(
            self._context, round_number, output, len(output), ctypes.byref(written)
        ) or not 1 <= written.value <= MAX_SHARE:
            raise PairingProtocolError("Could not write EC J-PAKE round")
        return output.raw[:written.value]

    def read(self, round_number, data):
        if self._dll.ulab_pake_read(self._context, round_number, data, len(data)):
            raise PairingProtocolError("Invalid EC J-PAKE round")

    def derive(self):
        output = ctypes.create_string_buffer(32)
        try:
            if self._dll.ulab_pake_derive(self._context, output):
                raise PairingProtocolError("Could not derive EC J-PAKE secret")
            return output.raw
        finally:
            ctypes.memset(output, 0, len(output))


def derive_keys(secret, transcript):
    """Return the ULAB PSK and two independent confirmation keys."""
    return tuple(HKDF(
        algorithm=hashes.SHA256(), length=32, salt=transcript, info=label,
    ).derive(secret) for label in _KEY_LABELS)


async def _send(writer, stage, body):
    record = _HEADER.pack(MAGIC, stage, len(body)) + body
    writer.write(record)
    await writer.drain()
    return record


async def _receive(reader, stage, initial_magic=None):
    if initial_magic is None:
        header = await reader.readexactly(_HEADER.size)
    else:
        header = initial_magic + await reader.readexactly(_HEADER.size - len(MAGIC))
    magic, received_stage, length = _HEADER.unpack(header)
    valid_length = (1 <= length <= MAX_SHARE) if stage <= 4 else length == TAG_LENGTH
    if magic != MAGIC or received_stage != stage or not valid_length:
        raise PairingProtocolError("Invalid pairing record header")
    body = await reader.readexactly(length)
    return header + body, body


def _tag(key, transcript, role):
    return hmac.digest(key, b"ULABP001/" + role + b"\0" + transcript, "sha256")


async def _pair(reader, writer, code, *, client, timeout, initial_magic=None):
    try:
        async with asyncio.timeout(timeout):
            with _Exchange(code, client=client) as exchange:
                records = []
                for round_number in (1, 2):
                    client_stage = 2 * round_number - 1
                    server_stage = client_stage + 1
                    if client:
                        records.append(await _send(writer, client_stage, exchange.write(round_number)))
                        record, peer = await _receive(reader, server_stage)
                        exchange.read(round_number, peer)
                        records.append(record)
                    else:
                        first = initial_magic if round_number == 1 else None
                        record, peer = await _receive(reader, client_stage, first)
                        exchange.read(round_number, peer)
                        records.append(record)
                        records.append(await _send(writer, server_stage, exchange.write(round_number)))
                transcript = hashlib.sha256(b"".join(records)).digest()
                psk, client_key, server_key = derive_keys(exchange.derive(), transcript)

            client_tag = _tag(client_key, transcript, b"C")
            server_tag = _tag(server_key, transcript, b"S")
            if client:
                await _send(writer, 5, client_tag)
                _, peer = await _receive(reader, 6)
                if not hmac.compare_digest(server_tag, peer):
                    raise PairingProtocolError("Pairing confirmation failed")
            else:
                _, peer = await _receive(reader, 5)
                if not hmac.compare_digest(client_tag, peer):
                    raise PairingProtocolError("Pairing confirmation failed")
                await _send(writer, 6, server_tag)
            return psk
    except BaseException as error:
        writer.close()
        if isinstance(error, (TimeoutError, OSError, asyncio.IncompleteReadError)):
            raise PairingProtocolError("Pairing timed out or stream ended") from error
        raise


async def pair_client(reader, writer, code, *, timeout=PAIRING_TIMEOUT):
    return await _pair(reader, writer, code, client=True, timeout=timeout)


async def pair_server(reader, writer, code, *, timeout=PAIRING_TIMEOUT, initial_magic=None):
    """Synthetic conformance peer used by Windows tests."""
    return await _pair(reader, writer, code, client=False, timeout=timeout, initial_magic=initial_magic)
