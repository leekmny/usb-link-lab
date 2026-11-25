"""Fixed-port ULAB stream over the Windows Apple Mobile Device Service.

Wire reference: libusbmuxd 2.1.1, src/libusbmuxd.c and include/usbmuxd-proto.h.
"""

import asyncio
import hashlib
import plistlib
import socket
import struct
from dataclasses import dataclass
from xml.parsers.expat import ExpatError

from . import windows_usb


_DAEMON = ("127.0.0.1", 27015)
_DEVICE_PORT = 43123
_HEADER = struct.Struct("<IIII")
_VERSION, _PLIST = 1, 8
_MAX_PACKET = 256 * 1024
_MAX_DEVICES = 256
_TIMEOUT = 5.0


class TransportError(Exception):
    """A stable, sanitized transport failure code suitable for display."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Device:
    stable_id: str
    label: str

    @property
    def id(self) -> str:
        return self.stable_id


def _stable_id(serial: str) -> str:
    normalized = serial.lower().replace("-", "")
    return hashlib.sha256(b"USBLinkLab/USB/device\0" + normalized.encode("ascii")).hexdigest()


def _usb_devices(reply: dict) -> dict[Device, int]:
    rows = reply.get("DeviceList")
    if not isinstance(rows, list) or len(rows) > _MAX_DEVICES:
        raise TransportError("mux_protocol")

    devices = {}
    seen_handles = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("Properties"), dict):
            raise TransportError("mux_protocol")

        props = row["Properties"]
        handle = props.get("DeviceID", row.get("DeviceID"))
        outer_handle = row.get("DeviceID", handle)
        if (type(handle) is not int or not 0 < handle <= 0xFFFFFFFF
                or type(outer_handle) is not int or outer_handle != handle
                or handle in seen_handles):
            raise TransportError("mux_protocol")
        seen_handles.add(handle)

        if props.get("ConnectionType") != "USB":
            continue
        serial = props.get("SerialNumber")
        if (not isinstance(serial, str) or not serial.isascii() or not 1 <= len(serial) <= 128
                or not serial.replace("-", "").isalnum()):
            raise TransportError("mux_protocol")

        stable_id = _stable_id(serial)
        device = Device(stable_id, f"USB device {stable_id[:8].upper()}")
        if device in devices:
            raise TransportError("mux_protocol")
        devices[device] = handle
    return devices


async def _open_service() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        pid = await asyncio.to_thread(windows_usb.service_pid)
    except windows_usb.WindowsUSBError as error:
        raise TransportError(error.code) from None

    try:
        reader, writer = await asyncio.open_connection(*_DAEMON, family=socket.AF_INET)
    except OSError:
        raise TransportError("mux_unavailable") from None

    try:
        # Compare the server-side row of this connection with a second SCM query.
        owner = await asyncio.to_thread(
            windows_usb.connected_owner,
            writer.get_extra_info("peername"),
            writer.get_extra_info("sockname"),
        )
        current_pid = await asyncio.to_thread(windows_usb.service_pid)
        if owner != pid or owner != current_pid:
            raise TransportError("mux_owner_mismatch")
    except windows_usb.WindowsUSBError as error:
        writer.close()
        raise TransportError(error.code) from None
    except (OSError, TransportError, asyncio.CancelledError):
        writer.close()
        raise
    return reader, writer


async def _exchange(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                    tag: int, message: str, **fields: int) -> dict:
    request = plistlib.dumps({
        "MessageType": message,
        "ClientVersionString": "USBLinkLab",
        "ProgName": "USBLinkLab",
        "kLibUSBMuxVersion": 3,
        **fields,
    })
    writer.write(_HEADER.pack(_HEADER.size + len(request), _VERSION, _PLIST, tag) + request)
    await writer.drain()
    size, version, kind, reply_tag = _HEADER.unpack(await reader.readexactly(_HEADER.size))
    if not _HEADER.size < size <= _MAX_PACKET or (version, kind, reply_tag) != (_VERSION, _PLIST, tag):
        raise TransportError("mux_protocol")
    data = await reader.readexactly(size - _HEADER.size)
    try:
        result = plistlib.loads(data, fmt=plistlib.FMT_XML)
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError,
            RecursionError, IndexError, AttributeError, ExpatError):
        raise TransportError("mux_protocol") from None
    if not isinstance(result, dict):
        raise TransportError("mux_protocol")
    return result


async def _list_and_connect(device: Device | None):
    writer = None
    try:
        async with asyncio.timeout(_TIMEOUT):
            reader, writer = await _open_service()
            devices = _usb_devices(await _exchange(reader, writer, 1, "ListDevices"))
            if device is None:
                return sorted(devices, key=lambda item: item.stable_id)

            matching = [handle for item, handle in devices.items() if item.stable_id == device.stable_id]
            if not matching:
                raise TransportError("device_gone")
            reply = await _exchange(
                reader, writer, 2, "Connect",
                DeviceID=matching[0], PortNumber=socket.htons(_DEVICE_PORT)
            )
            status = reply.get("Number")
            if (reply.get("MessageType") != "Result" or type(status) is not int
                    or not 0 <= status <= 0xFFFFFFFF):
                raise TransportError("mux_protocol")
            if status:
                code = {2: "device_gone", 3: "listener_refused"}.get(status, "connect_rejected")
                raise TransportError(code)

            connected = writer
            writer = None
            return reader, connected
    except TimeoutError:
        raise TransportError("mux_timeout") from None
    except asyncio.IncompleteReadError:
        raise TransportError("mux_protocol") from None
    except OSError:
        raise TransportError("mux_unavailable") from None
    finally:
        if writer is not None:
            writer.close()


async def discover() -> list[Device]:
    """List devices explicitly marked USB by the locally verified service."""
    return await _list_and_connect(None)


async def connect(device: Device) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Recheck the chosen USB identity and open the app stream on the same socket."""
    return await _list_and_connect(device)
