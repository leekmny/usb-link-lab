"""Exercise the real native PAKE bridge over local synthetic streams."""

import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from usblinklab.pin_pairing import (
    MAGIC, PairingProtocolError, _receive, _tag, derive_keys, pair_client, pair_server,
)
from usblinklab.protocol import handshake


def test_pairing_key_schedule_matches_hkdf_extract_and_expand():
    secret = bytes(range(32))
    transcript = hashlib.sha256(b"public unit test transcript").digest()
    extracted = hmac.digest(transcript, secret, "sha256")
    labels = (b"ulab-ecjpake/1/psk", b"ulab-ecjpake/1/client-confirm", b"ulab-ecjpake/1/server-confirm")
    expected = tuple(hmac.digest(extracted, label + b"\x01", "sha256") for label in labels)
    assert derive_keys(secret, transcript) == expected
    assert len(set(expected)) == 3


async def exchange(client_code, server_code, *, run_regular_handshake=False):
    server_result = asyncio.get_running_loop().create_future()

    async def accept(reader, writer):
        try:
            prefix = await reader.readexactly(8)
            secret = await pair_server(reader, writer, server_code, initial_magic=prefix)
            if run_regular_handshake:
                await handshake(reader, writer, secret, client=False)
            server_result.set_result(secret)
        except (PairingProtocolError, asyncio.IncompleteReadError) as exc:
            server_result.set_result(exc)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname())
        try:
            client_result = await pair_client(reader, writer, client_code)
            if run_regular_handshake:
                await handshake(reader, writer, client_result, client=True)
        except PairingProtocolError as exc:
            client_result = exc
        finally:
            writer.close()
            await writer.wait_closed()
        return client_result, await asyncio.wait_for(server_result, 5)
    finally:
        server.close()
        await server.wait_closed()


def test_six_digit_code_with_leading_zero_derives_fresh_psk_and_starts_v1():
    first, peer = asyncio.run(exchange("004271", "004271", run_regular_handshake=True))
    second, second_peer = asyncio.run(exchange("004271", "004271"))
    assert isinstance(first, bytes) and len(first) == 32
    assert first == peer
    assert second == second_peer and second != first


def test_wrong_code_never_returns_pairing_key():
    client, server = asyncio.run(exchange("004271", "004272"))
    assert isinstance(client, PairingProtocolError)
    assert isinstance(server, PairingProtocolError)


@pytest.mark.parametrize("bad", ["12345", "1234567", "０１２３４５", "12 456", "12-456"])
def test_non_ascii_or_non_six_digit_code_is_rejected(bad):
    async def exercise():
        class UnusedWriter:
            def close(self):
                pass

        with pytest.raises(PairingProtocolError, match="six ASCII digits"):
            await pair_client(asyncio.StreamReader(), UnusedWriter(), bad)

    asyncio.run(exercise())


@pytest.mark.parametrize("response", [
    b"ULAB0001" + bytes((2, 0, 32)),
    b"ULABP001" + bytes((4, 0, 32)),
    b"ULABP001" + bytes((2, 2, 1)),
])
def test_wrong_header_fails_before_reading_variable_body(response):
    async def exercise():
        class Writer:
            closed = False

            def write(self, data):
                assert data.startswith(MAGIC)

            async def drain(self):
                pass

            def close(self):
                self.closed = True

        reader = asyncio.StreamReader()
        reader.feed_data(response)
        writer = Writer()
        with pytest.raises(PairingProtocolError, match="record header"):
            await asyncio.wait_for(pair_client(reader, writer, "123456"), 3)
        assert writer.closed

    asyncio.run(exercise())



def test_public_pairing_vector_matches_framing_derivation_and_tags():
    fixture = Path(__file__).resolve().parents[3] / "protocol" / "vectors" / "pairing-v1.json"
    vector = json.loads(fixture.read_text(encoding="utf-8"))
    records = [bytes.fromhex(value) for value in vector["record_hex_in_order"]]

    async def verify_fragments():
        for stage, record in enumerate(records, start=1):
            reader = asyncio.StreamReader()
            for byte in record:
                reader.feed_data(bytes([byte]))
            raw, body = await _receive(reader, stage)
            assert raw == record and body == bytes((0, stage, 0xff))

    asyncio.run(verify_fragments())
    transcript = hashlib.sha256(b"".join(records)).digest()
    assert transcript.hex() == vector["transcript_sha256"]
    psk, client, server = derive_keys(bytes.fromhex(vector["bridge_derived_secret_hex"]), transcript)
    expected = vector["hkdf_sha256"]
    assert (psk.hex(), client.hex(), server.hex()) == (expected["psk"], expected["client-confirm"], expected["server-confirm"])
    assert _tag(client, transcript, b"C").hex() == vector["confirmation_hex"]["C"]
    assert _tag(server, transcript, b"S").hex() == vector["confirmation_hex"]["S"]
