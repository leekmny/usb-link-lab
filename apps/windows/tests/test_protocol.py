from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import zlib

import pytest


VECTORS = Path(__file__).resolve().parents[3] / "protocol" / "vectors"
PSK = bytes(range(32))
ITEM = bytes(range(16))
KEYS = tuple(bytes([index]) * 32 for index in range(5))


@pytest.fixture(scope="session")
def codec():
    assert importlib.util.find_spec("usblinklab.protocol"), "ULAB codec is missing"
    return importlib.import_module("usblinklab.protocol")


def as_bytes(value):
    return bytes.fromhex(value)


async def read_wire(codec, wire, keys, sequence, *, cuts=()):
    reader = asyncio.StreamReader()

    async def feed():
        start = 0
        for end in (*cuts, len(wire)):
            reader.feed_data(wire[start:end])
            start = end
            await asyncio.sleep(0)
        reader.feed_eof()

    feed_task = asyncio.create_task(feed())
    try:
        return await codec.read_frame(reader, keys, sequence)
    finally:
        await feed_task


def test_frame_matches_canonical_aes_bytes(codec):
    keys = codec.DirectionKeys(
        as_bytes("f23507808fa3b0ce6e3106d9797939e3bbedca8196249eef2df2249f54ecfd8d"),
        KEYS[1],
        as_bytes("436418f1f2ee0db243a50b6c7312b5e68db32a59d0331bce7e014f525b7a1972"),
        KEYS[3],
        KEYS[4],
    )
    frame = codec.Frame(
        codec.FrameType.DATA, codec.Mode.AES256GCM, 2, ITEM, 0,
        as_bytes("ec9588eb85952c20555342210a"),
    )
    wire = as_bytes(
        "00000065"
        "010301000000000000000002000102030405060708090a0b0c0d0e0f00000000000000000000000d"
        "69b21eeb63dee7bf2b1ea369dba6aab155a750941a5031afc0d4691ac550c4eb"
        "bb849151471526ecff5220478dd9a5931597bfac524fc3b554dfcb4ee1"
    )
    assert codec.encode_frame(frame, keys) == wire
    assert asyncio.run(read_wire(codec, wire, keys, 2, cuts=range(1, len(wire)))) == frame


def test_reads_two_coalesced_records(codec):
    keys = codec.DirectionKeys(*KEYS)
    f1 = codec.Frame(codec.FrameType.OPEN, codec.Mode.HMACSHA256, 0, ITEM, 0, b'{"mode":3}')
    f2 = codec.Frame(codec.FrameType.DATA, codec.Mode.HMACSHA256, 1, ITEM, 0, b'\x00\xff')

    async def receive():
        reader = asyncio.StreamReader()
        reader.feed_data(codec.encode_frame(f1, keys) + codec.encode_frame(f2, keys))
        assert await codec.read_frame(reader, keys, 0) == f1
        assert await codec.read_frame(reader, keys, 1) == f2

    asyncio.run(receive())


@pytest.mark.parametrize("prefix", (0, 71, 1_048_577, 0xffff_ffff))
def test_rejects_oversized_prefix_without_waiting_for_header(codec, prefix):
    async def receive():
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", prefix))
        with pytest.raises(codec.ProtocolError):
            await asyncio.wait_for(codec.read_frame(reader, codec.DirectionKeys(*KEYS), 0), 0.2)

    asyncio.run(receive())


def test_rejects_bad_header_mac_before_waiting_for_body(codec):
    keys = codec.DirectionKeys(*KEYS)
    frame = codec.Frame(codec.FrameType.DATA, codec.Mode.PLAINTEXT, 0, ITEM, 0, b"x")
    invalid_header = bytearray(codec.encode_frame(frame, keys)[:76])
    invalid_header[12] ^= 1

    async def receive():
        reader = asyncio.StreamReader()
        reader.feed_data(invalid_header)
        with pytest.raises(codec.AuthenticationError):
            await asyncio.wait_for(codec.read_frame(reader, keys, 0), 0.2)

    asyncio.run(receive())


@pytest.mark.parametrize("cut", (0, 1, 4, 43, 44, 75, 76, 77, 88))
def test_rejects_eof_at_each_framing_boundary(codec, cut):
    keys = codec.DirectionKeys(*KEYS)
    frame = codec.Frame(codec.FrameType.DATA, codec.Mode.HMACSHA256, 0, ITEM, 0, b"payload")
    wire = codec.encode_frame(frame, keys)
    error_type = codec.EndOfStream if cut == 0 else codec.ProtocolError
    with pytest.raises(error_type) as error:
        asyncio.run(read_wire(codec, wire[:cut], keys, 0))
    assert type(error.value) is error_type


def test_idle_wait_is_outside_the_frame_deadline(codec, monkeypatch):
    monkeypatch.setattr(codec, "FRAME_TIMEOUT", 0.025)
    keys = codec.DirectionKeys(*KEYS)
    frame = codec.Frame(codec.FrameType.DATA, codec.Mode.PLAINTEXT, 0, ITEM, 0, b"x")

    async def idle_then_receive():
        reader = asyncio.StreamReader()
        receive = asyncio.create_task(codec.read_frame(reader, keys, 0))
        await asyncio.sleep(0.06)
        assert not receive.done()
        reader.feed_data(codec.encode_frame(frame, keys))
        assert await receive == frame

    async def stall_after_first_byte():
        reader = asyncio.StreamReader()
        reader.feed_data(b"\0")
        with pytest.raises(codec.ProtocolError, match="timed out"):
            await asyncio.wait_for(codec.read_frame(reader, keys, 0), 0.3)

    asyncio.run(idle_then_receive())
    asyncio.run(stall_after_first_byte())


@pytest.mark.parametrize("body", (
    b'{"mode":0,"mode":0}', br'{"mode":0,"\u006dode":0}',
    b'{"mode":false}', b'{"mode":0.0}', b'{"mode":0e0}', b'{"mode":-0}',
    b'{"mode":+0}', b'{"mode":00}', b'{"mode":18446744073709551616}',
    b'{"mode":NaN}', b'{"mode":"0"}', b'{"mode":{}}', b'{"mode":[]}',
    b'{"mode":null}', b'{"mode":0,"extra":1}', b'{}', b'[]',
    b'{"mode":0} extra', b'\xef\xbb\xbf{"mode":0}', b'{"mode":\xff}',
    br'{"\ud800":0}', b'{"mode":1}',
))
def test_schema_rejects_invalid_json_or_open_mode(codec, body):
    frame = codec.Frame(codec.FrameType.OPEN, codec.Mode.PLAINTEXT, 0, ITEM, 0, body)
    with pytest.raises(codec.ProtocolError):
        codec.decode_body(frame)


def test_schema_accepts_insignificant_whitespace_and_escaped_keys(codec):
    frame = codec.Frame(
        codec.FrameType.OPEN, codec.Mode.PLAINTEXT, 0, ITEM, 0, br'  { "\u006dode" : 0 }  ',
    )
    assert codec.decode_body(frame) == {"mode": 0}


@pytest.mark.parametrize("name", (
    "", ".", "..", "../payload", "part/name", "part\\name", "name\x00.txt",
    "name\n.txt", "name\x85.txt", "C:payload", "name ", "name.", "CON",
    "NUL.txt", "CONIN$", "conout$.txt", "LPT\u00b2.txt", "COM1 .txt", "a" * 256, "\U0001f984" * 64, "\ud800",
))
def test_meta_rejects_unsafe_or_overlong_filename(codec, name):
    body = json.dumps({
        "kind": "file", "name": name, "media_type": "application/octet-stream",
        "size": 0, "chunk_size": 65_536,
    }).encode()
    frame = codec.Frame(codec.FrameType.META, codec.Mode.PLAINTEXT, 1, ITEM, 0, body)
    with pytest.raises(codec.ProtocolError):
        codec.decode_body(frame)


def test_meta_accepts_valid_window_safe_unicode_filename(codec):
    original = {
        "kind": "image", "name": "\ud55c\uae00-\U0001f984.png", "media_type": "image/png",
        "size": 1_073_741_824, "chunk_size": 65_536,
    }
    frame = codec.Frame(codec.FrameType.META, codec.Mode.HMACSHA256, 1, ITEM, 0, codec.encode_body(original))
    assert codec.decode_body(frame) == original


def load_vectors(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def load_frame(codec, record):
    return codec.Frame(
        codec.FrameType[record["type"]], codec.Mode[record["mode"]], record["sequence"],
        as_bytes(record["item_id_hex"]), record["index"], as_bytes(record["body_hex"]),
    )


def load_keys(codec, corpus, direction):
    return codec.DirectionKeys(**{
        name: as_bytes(value) for name, value in corpus["keys_hex"][direction].items()
    })


class RecordingWriter:
    def __init__(self, writer):
        self.writer = writer
        self.written = bytearray()

    def write(self, data):
        self.written.extend(data)
        self.writer.write(data)

    async def drain(self):
        await self.writer.drain()

    def close(self):
        self.writer.close()


def test_real_loopback_handshake_matches_all_transcript_bytes_and_keys(codec, monkeypatch):
    corpus = load_vectors("v1-positive.json")
    hs = corpus["handshake_hex"]
    nonces = iter((as_bytes(hs["client_nonce"]), as_bytes(hs["server_nonce"])))
    monkeypatch.setattr(codec.secrets, "token_bytes", lambda size: next(nonces))

    async def connect():
        server_finished = asyncio.get_running_loop().create_future()

        async def accept(reader, writer):
            recorded = RecordingWriter(writer)
            try:
                keys = await codec.handshake(reader, recorded, PSK, client=False)
                server_finished.set_result((keys, recorded.written))
            except Exception as error:
                server_finished.set_exception(error)
            finally:
                writer.close()
                await writer.wait_closed()

        async with await asyncio.start_server(accept, "127.0.0.1", 0) as server:
            reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname())
            recorded = RecordingWriter(writer)
            try:
                client_keys = await codec.handshake(reader, recorded, PSK, client=True)
                server_keys, server_written = await asyncio.wait_for(server_finished, 0.5)
            finally:
                writer.close()
                await writer.wait_closed()

        c2s, s2c = (load_keys(codec, corpus, direction) for direction in ("c2s", "s2c"))
        assert client_keys == (c2s, s2c)
        assert server_keys == (s2c, c2s)
        assert recorded.written == as_bytes(hs["client_hello"] + hs["client_finish"])
        assert server_written == as_bytes(hs["server_hello"])
        transcript = recorded.written[:73] + server_written + recorded.written[73:]
        assert hashlib.sha256(transcript).hexdigest() == hs["transcript_hash"]

    asyncio.run(connect())


def test_independent_positive_records_match_encoder_and_decoder(codec):
    corpus = load_vectors("v1-positive.json")

    async def verify():
        for scenario in corpus["scenarios"]:
            next_sequence = {"c2s": 0, "s2c": 0}
            for record in scenario["records"]:
                direction = record["direction"]
                keys = load_keys(codec, corpus, direction)
                frame, wire = load_frame(codec, record), as_bytes(record["wire_hex"])
                assert frame.sequence == next_sequence[direction], scenario["name"]
                assert codec.encode_frame(frame, keys) == wire, scenario["name"]
                assert await read_wire(codec, wire, keys, frame.sequence) == frame, scenario["name"]
                next_sequence[direction] += 1

    asyncio.run(verify())


def test_independent_negative_frame_records_are_rejected(codec):
    positive = load_vectors("v1-positive.json")
    negative = load_vectors("v1-negative.json")
    error_types = {"authentication": codec.AuthenticationError, "protocol": codec.ProtocolError}

    async def verify():
        for case in negative["frames"]:
            keys = load_keys(codec, positive, case["direction"])
            try:
                await read_wire(codec, as_bytes(case["wire_hex"]), keys, case["expected_sequence"])
            except error_types[case["error"]] as error:
                if case["error"] == "protocol":
                    assert not isinstance(error, codec.AuthenticationError), case["name"]
            else:
                pytest.fail(f"Accepted invalid frame {case['name']}")

    asyncio.run(verify())


def test_independent_negative_handshakes_close_and_reject(codec, monkeypatch):
    positive = load_vectors("v1-positive.json")
    negative = load_vectors("v1-negative.json")

    class BufferedWriter:
        def __init__(self):
            self.closed = False

        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    async def verify():
        handshake_hex = positive["handshake_hex"]
        for case in negative["handshakes"]:
            nonce = handshake_hex["client_nonce" if case["role"] == "client" else "server_nonce"]
            monkeypatch.setattr(codec.secrets, "token_bytes", lambda size: as_bytes(nonce))
            reader = asyncio.StreamReader()
            reader.feed_data(as_bytes(case["received_hex"]))
            reader.feed_eof()
            writer = BufferedWriter()
            with pytest.raises(codec.AuthenticationError):
                await codec.handshake(reader, writer, as_bytes(case["psk_hex"]), client=case["role"] == "client")
            assert writer.closed, case["name"]

    asyncio.run(verify())



def test_synthetic_image_fixture_is_valid_png():
    corpus = load_vectors("v1-positive.json")
    records = corpus["scenarios"][0]["records"]
    image = as_bytes(next(record["body_hex"] for record in records
                          if record["direction"] == "s2c" and record["type"] == "DATA"))
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    chunks = {}
    while offset < len(image):
        length, kind = struct.unpack_from(">I4s", image, offset)
        start, end = offset + 8, offset + 8 + length
        payload = image[start:end]
        expected_crc, = struct.unpack_from(">I", image, end)
        assert zlib.crc32(kind + payload) == expected_crc, kind
        chunks[kind] = payload
        offset = end + 4
    assert offset == len(image)
    assert chunks.keys() == {b"IHDR", b"IDAT", b"IEND"}
    assert struct.unpack(">II", chunks[b"IHDR"][:8]) == (1, 1)
    assert len(zlib.decompress(chunks[b"IDAT"])) > 1
