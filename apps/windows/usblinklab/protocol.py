"""ULAB v1 authenticated handshake, frame codec and body schemas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import hmac
import json
import re
import secrets
import struct
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


CHUNK_SIZE = 65_536
MAX_JSON_SIZE = 4_096
MAX_TEXT_SIZE = 1_048_576
MAX_FILE_SIZE = 1_073_741_824
MAX_FRAME_SIZE = 1_048_576
MAX_FRAME_COUNT = 1 << 32
MAX_SEQUENCE = MAX_FRAME_COUNT - 1
MAX_BODY_BYTES = 1 << 32
MAX_ITEM_IDS = 4_096
HANDSHAKE_TIMEOUT = 10
FRAME_TIMEOUT = 10

_MAGIC = b"ULAB0001"
_ZERO_ID = bytes(16)
_PREFIX = struct.Struct(">I")
_HEADER = struct.Struct(">BBBBQ16sQI")
_MAC_SIZE = 32
_FIXED_SIZE = _HEADER.size + _MAC_SIZE
_UINT64_MAX = (1 << 64) - 1
_TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_WINDOWS_DEVICE = re.compile(r"CON(?:IN\$|OUT\$)?|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", re.ASCII | re.IGNORECASE)


class ProtocolError(Exception):
    """An invalid record or failed protocol operation."""


class AuthenticationError(ProtocolError):
    """A handshake or frame authentication check failed."""


class EndOfStream(ProtocolError):
    """The peer closed its stream between frames."""


class Mode(IntEnum):
    PLAINTEXT = 0
    AES256GCM = 1
    CHACHA20POLY1305 = 2
    HMACSHA256 = 3


class FrameType(IntEnum):
    OPEN = 1
    META = 2
    DATA = 3
    END = 4
    ACK = 5
    ERROR = 6
    DRAIN = 7


@dataclass(frozen=True, slots=True)
class DirectionKeys:
    hdr: bytes
    ctl: bytes
    aes: bytes
    chacha: bytes
    hmac: bytes


@dataclass(frozen=True, slots=True)
class Frame:
    type: FrameType
    mode: Mode
    sequence: int
    item_id: bytes
    index: int
    body: bytes


_CONTROL_TYPES = {FrameType.OPEN, FrameType.ACK, FrameType.ERROR, FrameType.DRAIN}
_ZERO_ID_TYPES = {FrameType.ERROR, FrameType.DRAIN}
_ZERO_INDEX_TYPES = _CONTROL_TYPES | {FrameType.META}
_OVERHEAD = {
    Mode.PLAINTEXT: 0,
    Mode.AES256GCM: 16,
    Mode.CHACHA20POLY1305: 16,
    Mode.HMACSHA256: _MAC_SIZE,
}


def _mac(key: bytes, data: bytes) -> bytes:
    return hmac.digest(key, data, "sha256")


def _verify_mac(key: bytes, data: bytes, tag: bytes) -> None:
    if not hmac.compare_digest(_mac(key, data), tag):
        raise AuthenticationError("Message authentication failed")


def _record(psk: bytes, kind: int, nonce: bytes, domain: bytes, prior: bytes = b"") -> bytes:
    payload = _MAGIC + bytes([kind]) + nonce
    return payload + _mac(psk, domain + prior + payload)


def _verify_record(psk: bytes, record: bytes, kind: int, domain: bytes, prior: bytes = b"") -> None:
    payload, tag = record[:-_MAC_SIZE], record[-_MAC_SIZE:]
    if payload[:9] != _MAGIC + bytes([kind]):
        raise AuthenticationError("Unsupported handshake record")
    _verify_mac(psk, domain + prior + payload, tag)


def _derive_keys(psk: bytes, transcript_hash: bytes, direction: str) -> DirectionKeys:
    return DirectionKeys(**{
        purpose: HKDF(
            algorithm=hashes.SHA256(), length=32, salt=transcript_hash,
            info=f"ULAB1/{direction}/{purpose}".encode("ascii"),
        ).derive(psk)
        for purpose in ("hdr", "ctl", "aes", "chacha", "hmac")
    })


async def _write(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(data)
    await writer.drain()


async def handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    psk: bytes,
    *,
    client: bool,
) -> tuple[DirectionKeys, DirectionKeys]:
    """Run the handshake and return the outbound and inbound keys in that order."""
    try:
        if not isinstance(psk, bytes) or len(psk) != 32:
            raise AuthenticationError("Pairing requires a 32-byte secret")
        async with asyncio.timeout(HANDSHAKE_TIMEOUT):
            if client:
                ch = _record(psk, 1, secrets.token_bytes(32), b"ULAB1-CH\0")
                await _write(writer, ch)
                sh = await reader.readexactly(73)
                _verify_record(psk, sh, 2, b"ULAB1-SH\0", ch)
                cf = _record(psk, 3, b"", b"ULAB1-CF\0", ch + sh)
                await _write(writer, cf)
            else:
                ch = await reader.readexactly(73)
                _verify_record(psk, ch, 1, b"ULAB1-CH\0")
                sh = _record(psk, 2, secrets.token_bytes(32), b"ULAB1-SH\0", ch)
                await _write(writer, sh)
                cf = await reader.readexactly(41)
                _verify_record(psk, cf, 3, b"ULAB1-CF\0", ch + sh)
    except TimeoutError as error:
        writer.close()
        raise ProtocolError("Handshake timed out") from error
    except asyncio.IncompleteReadError as error:
        writer.close()
        raise ProtocolError("Stream closed during handshake") from error
    except BaseException:
        writer.close()
        raise

    transcript_hash = hashlib.sha256(ch + sh + cf).digest()
    c2s = _derive_keys(psk, transcript_hash, "c2s")
    s2c = _derive_keys(psk, transcript_hash, "s2c")
    return (c2s, s2c) if client else (s2c, c2s)


def _validate_header_fields(
    frame_type: FrameType, mode: Mode, sequence: int, item_id: bytes, index: int, body_length: int,
) -> None:
    if type(sequence) is not int or not 0 <= sequence <= MAX_SEQUENCE:
        raise ProtocolError("Sequence exceeds the session counter limit")
    if not isinstance(item_id, bytes) or len(item_id) != 16:
        raise ProtocolError("Invalid item ID length")
    if (item_id == _ZERO_ID) != (frame_type in _ZERO_ID_TYPES):
        raise ProtocolError("Invalid item ID for the frame type")
    if type(index) is not int or not 0 <= index <= _UINT64_MAX:
        raise ProtocolError("Invalid chunk index")
    if frame_type in _ZERO_ID_TYPES and mode != Mode.PLAINTEXT:
        raise ProtocolError("This frame type requires mode zero")
    if frame_type in _ZERO_INDEX_TYPES and index != 0:
        raise ProtocolError("This frame type requires chunk index zero")
    if frame_type == FrameType.DATA:
        if not 1 <= body_length <= CHUNK_SIZE:
            raise ProtocolError("Invalid DATA length")
    elif body_length > MAX_JSON_SIZE:
        raise ProtocolError("JSON exceeds the frame body limit")


def _nonce(sequence: int) -> bytes:
    return struct.pack(">IQ", 0, sequence)


def _body_domains(prefix: bytes, header: bytes) -> tuple[bytes, bytes]:
    return b"ULAB1-PAYLOAD\0" + prefix + header, b"ULAB1-CTL\0" + prefix + header


def encode_frame(frame: Frame, keys: DirectionKeys) -> bytes:
    """Encode one frame using the keys of its sender direction."""
    try:
        frame_type, mode = FrameType(frame.type), Mode(frame.mode)
    except ValueError as error:
        raise ProtocolError("Unknown frame type or mode") from error
    if not isinstance(frame.body, bytes):
        raise ProtocolError("Frame body must be bytes")

    body_length = len(frame.body)
    _validate_header_fields(frame_type, mode, frame.sequence, frame.item_id, frame.index, body_length)
    if frame_type != FrameType.DATA:
        decode_body(frame)
    is_control = frame_type in _CONTROL_TYPES
    overhead = _MAC_SIZE if is_control else _OVERHEAD[mode]
    prefix = _PREFIX.pack(_FIXED_SIZE + body_length + overhead)
    header = _HEADER.pack(1, frame_type, mode, 0, frame.sequence, frame.item_id, frame.index, body_length)
    header_mac = _mac(keys.hdr, b"ULAB1-HDR\0" + prefix + header)
    aad, control = _body_domains(prefix, header)

    body = frame.body
    if is_control:
        body += _mac(keys.ctl, control + body)
    elif mode == Mode.HMACSHA256:
        body += _mac(keys.hmac, aad + body)
    elif mode == Mode.AES256GCM:
        body = AESGCM(keys.aes).encrypt(_nonce(frame.sequence), body, aad)
    elif mode == Mode.CHACHA20POLY1305:
        body = ChaCha20Poly1305(keys.chacha).encrypt(_nonce(frame.sequence), body, aad)
    return prefix + header + header_mac + body


async def read_frame(
    reader: asyncio.StreamReader, keys: DirectionKeys, expected_sequence: int,
) -> Frame:
    """Read one exact frame and verify its header before reading its wire body."""
    try:
        first = await reader.readexactly(1)
    except asyncio.IncompleteReadError as error:
        raise EndOfStream("Stream closed between frames") from error

    try:
        async with asyncio.timeout(FRAME_TIMEOUT):
            prefix = first + await reader.readexactly(_PREFIX.size - 1)
            length = _PREFIX.unpack(prefix)[0]
            if not _FIXED_SIZE <= length <= MAX_FRAME_SIZE:
                raise ProtocolError("Invalid frame length")
            fixed = await reader.readexactly(_FIXED_SIZE)
            header, header_mac = fixed[:_HEADER.size], fixed[_HEADER.size:]
            _verify_mac(keys.hdr, b"ULAB1-HDR\0" + prefix + header, header_mac)
            version, type_value, mode_value, flags, sequence, item_id, index, body_length = _HEADER.unpack(header)
            if version != 1 or flags:
                raise ProtocolError("Unsupported frame version or flags")
            try:
                frame_type, mode = FrameType(type_value), Mode(mode_value)
            except ValueError as error:
                raise ProtocolError("Unknown frame type or mode") from error
            _validate_header_fields(frame_type, mode, sequence, item_id, index, body_length)
            if sequence != expected_sequence:
                raise ProtocolError("Frame sequence is out of order")
            is_control = frame_type in _CONTROL_TYPES
            overhead = _MAC_SIZE if is_control else _OVERHEAD[mode]
            if length != _FIXED_SIZE + body_length + overhead:
                raise ProtocolError("Frame length does not match the body and tag lengths")
            body = await reader.readexactly(body_length + overhead)
    except TimeoutError as error:
        raise ProtocolError("Frame read timed out") from error
    except asyncio.IncompleteReadError as error:
        raise ProtocolError("Stream closed during frame") from error

    aad, control = _body_domains(prefix, header)
    try:
        if is_control:
            body, tag = body[:body_length], body[body_length:]
            _verify_mac(keys.ctl, control + body, tag)
        elif mode == Mode.HMACSHA256:
            body, tag = body[:body_length], body[body_length:]
            _verify_mac(keys.hmac, aad + body, tag)
        elif mode == Mode.AES256GCM:
            body = AESGCM(keys.aes).decrypt(_nonce(sequence), body, aad)
        elif mode == Mode.CHACHA20POLY1305:
            body = ChaCha20Poly1305(keys.chacha).decrypt(_nonce(sequence), body, aad)
    except InvalidTag as error:
        raise AuthenticationError("Body authentication failed") from error

    frame = Frame(frame_type, mode, sequence, item_id, index, body)
    if frame_type != FrameType.DATA:
        decode_body(frame)
    return frame


def _scalar_utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ProtocolError("Invalid Unicode scalar string") from error


def validate_filename(name: str) -> None:
    """Validate a display filename before interpreting it on Windows."""
    if not isinstance(name, str) or not 1 <= len(_scalar_utf8(name)) <= 255:
        raise ProtocolError("Invalid filename length")
    if name.endswith((" ", ".")) or _WINDOWS_DEVICE.fullmatch(name.split(".", 1)[0].rstrip(" ")):
        raise ProtocolError("Unsafe filename")
    if any(char in '<>:"/\\|?*' or unicodedata.category(char) == "Cc" for char in name):
        raise ProtocolError("Unsafe filename")


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _scalar_utf8(key)
        if key in result:
            raise ProtocolError("Repeated JSON member name")
        if isinstance(value, str):
            _scalar_utf8(value)
        elif type(value) is not int:
            raise ProtocolError("JSON members must be strings or unsigned integers")
        result[key] = value
    return result


def _json_uint(token: str) -> int:
    if token.startswith("-") or len(token) > 20:
        raise ProtocolError("Invalid unsigned JSON integer")
    value = int(token)
    if value > _UINT64_MAX:
        raise ProtocolError("Unsigned JSON integer overflow")
    return value


def _reject_json_number(token: str) -> int:
    raise ProtocolError("Invalid unsigned JSON integer")


def _fields(body: dict, **types: type) -> None:
    if body.keys() != types.keys() or any(type(body[key]) is not kind for key, kind in types.items()):
        raise ProtocolError("Unexpected JSON fields or field types")


def _validate_meta(body: dict) -> None:
    _fields(body, kind=str, name=str, media_type=str, size=int, chunk_size=int)
    kind, name, media_type, size = (body[key] for key in ("kind", "name", "media_type", "size"))
    if body["chunk_size"] != CHUNK_SIZE:
        raise ProtocolError("Unsupported chunk size")
    if kind == "text":
        if name or media_type != _TEXT_MEDIA_TYPE or size > MAX_TEXT_SIZE:
            raise ProtocolError("Invalid text metadata")
    elif kind in ("image", "file"):
        validate_filename(name)
        if size > MAX_FILE_SIZE or not 1 <= len(media_type) <= 127:
            raise ProtocolError("Invalid file metadata size or media type length")
        if not all(" " <= char <= "~" for char in media_type):
            raise ProtocolError("Media type must be printable ASCII")
        if kind == "image" and not media_type.startswith("image/"):
            raise ProtocolError("Image metadata requires an image media type")
    else:
        raise ProtocolError("Unknown item kind")


def encode_body(body: dict) -> bytes:
    """Encode a JSON body. Its frame-specific schema is checked by encode_frame."""
    try:
        return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ProtocolError("Cannot encode JSON body") from error


def decode_body(frame: Frame) -> dict:
    """Decode and validate one authenticated frame's exact JSON body schema."""
    if frame.type == FrameType.DATA or len(frame.body) > MAX_JSON_SIZE:
        raise ProtocolError("Invalid JSON frame type or length")
    try:
        body = json.loads(
            frame.body.decode("utf-8"), object_pairs_hook=_json_object, parse_int=_json_uint,
            parse_float=_reject_json_number, parse_constant=_reject_json_number,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError("Malformed UTF-8 JSON body") from error
    if not isinstance(body, dict):
        raise ProtocolError("JSON body must be an object")

    match frame.type:
        case FrameType.OPEN:
            _fields(body, mode=int)
            if body["mode"] != frame.mode:
                raise ProtocolError("OPEN mode does not match the frame mode")
        case FrameType.META:
            _validate_meta(body)
        case FrameType.END:
            _fields(body, bytes=int, chunks=int, sha256=str)
            if body["bytes"] > MAX_FILE_SIZE or body["chunks"] > MAX_FILE_SIZE // CHUNK_SIZE:
                raise ProtocolError("END count exceeds the item limit")
            if body["chunks"] != frame.index or not _LOWERCASE_SHA256.fullmatch(body["sha256"]):
                raise ProtocolError("Invalid END chunk index or digest")
        case FrameType.ACK:
            _fields(body, stage=str)
            if body["stage"] not in ("ready", "complete"):
                raise ProtocolError("Invalid ACK stage")
        case FrameType.ERROR:
            _fields(body, code=str)
            if body["code"] != "protocol_error":
                raise ProtocolError("Invalid ERROR code")
        case FrameType.DRAIN:
            _fields(body, stage=str)
            if body["stage"] not in ("request", "ready"):
                raise ProtocolError("Invalid DRAIN stage")
        case _:
            raise ProtocolError("Unknown JSON frame type")
    return body
