"""Check companion-branch public vectors with the Windows codec, offline."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "windows"))

from usblinklab import PROTOCOL_REVISION
from usblinklab import protocol as wire


def require(condition, context):
    if not condition:
        raise ValueError(f"Fixture mismatch: {context}")


async def check(corpus):
    require(corpus["description"].startswith("PUBLIC TEST DATA."), "public test data label")
    require(corpus["revision"] == PROTOCOL_REVISION, "protocol revision")
    handshake = corpus["handshake"]
    psk = bytes.fromhex(handshake["psk"])
    require(psk == bytes(range(32)), "deterministic public key")
    nc, ns = (bytes.fromhex(handshake[key]) for key in ("nc", "ns"))
    ch = wire._record(psk, 1, nc, b"ULAB1-CH\0")
    sh = wire._record(psk, 2, ns, b"ULAB1-SH\0", ch)
    cf = wire._record(psk, 3, b"", b"ULAB1-CF\0", ch + sh)
    transcript = sha256(ch + sh + cf).digest()
    for key, value in zip(
        ("client_hello", "server_hello", "client_finish", "transcript_hash"), (ch, sh, cf, transcript),
        strict=True,
    ):
        require(value.hex() == handshake[key], key)
    keys = {direction: wire._derive_keys(psk, transcript, direction) for direction in ("c2s", "s2c")}
    for direction, purposes in handshake["keys"].items():
        for purpose, expected in purposes.items():
            require(getattr(keys[direction], purpose).hex() == expected, f"{direction}/{purpose}")

    records = 0
    for exchange in corpus["exchanges"]:
        for row in exchange["records"]:
            frame = wire.Frame(
                wire.FrameType(row["type"]), wire.Mode(exchange["mode"]), row["sequence"],
                bytes.fromhex(row["item_id"]), row["index"], bytes.fromhex(row["body"]),
            )
            expected = bytes.fromhex(row["record"])
            directional_keys = keys[row["direction"]]
            identity = f"{exchange['label']} {row['direction']}/{row['sequence']}"
            require(wire.encode_frame(frame, directional_keys) == expected, identity + " encode")
            reader = asyncio.StreamReader()
            reader.feed_data(expected)
            reader.feed_eof()
            actual = await wire.read_frame(reader, directional_keys, frame.sequence)
            require(actual == frame, identity + " decode")
            records += 1
    for entry in corpus["negative_bodies"]:
        frame = wire.Frame(wire.FrameType(entry["type"]), wire.Mode.PLAINTEXT,
                           0, bytes(range(16)), 0, bytes.fromhex(entry["body"]))
        try:
            wire.decode_body(frame)
        except wire.ProtocolError:
            continue
        raise ValueError(f"Accepted malformed JSON fixture: {entry['label']}")
    print(f"Companion fixture comparison passed: {records} records, "
          f"{len(corpus['negative_bodies'])} malformed bodies, one handshake.")


if __name__ == "__main__":
    asyncio.run(check(json.load(sys.stdin)))
