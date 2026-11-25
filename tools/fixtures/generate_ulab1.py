"""Generate byte-exact ULAB records using public, deterministic test data."""

import argparse
import hashlib
import hmac
import json
import struct
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).resolve().parents[2]
REVISION = "ULAB1-20261124-R1"
CONTRACT_HASH = "535a818b4e76c82bf7c782f9aa9482730b13865e1ddb8c3fb0cba2c946a6596f"
PSK, NC, NS = (bytes(range(start, start + 32)) for start in (0, 32, 64))
ITEM_ID = bytes(range(16))
TEXT = bytes.fromhex("ec9588eb85952c20555342210a")
CHUNK = 65_536
HEADER = struct.Struct(">BBBBQ16sQI")


def mac(key, data):
    return hmac.digest(key, data, "sha256")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def handshake():
    ch0, sh0, cf0 = b"ULAB0001\x01" + NC, b"ULAB0001\x02" + NS, b"ULAB0001\x03"
    ch = ch0 + mac(PSK, b"ULAB1-CH\0" + ch0)
    sh = sh0 + mac(PSK, b"ULAB1-SH\0" + ch + sh0)
    cf = cf0 + mac(PSK, b"ULAB1-CF\0" + ch + sh + cf0)
    transcript = hashlib.sha256(ch + sh + cf).digest()
    keys = {}
    for direction in ("c2s", "s2c"):
        keys[direction] = {
            purpose: HKDF(
                algorithm=hashes.SHA256(), length=32, salt=transcript,
                info=f"ULAB1/{direction}/{purpose}".encode("ascii"),
            ).derive(PSK)
            for purpose in ("hdr", "ctl", "aes", "chacha", "hmac")
        }
    assert ch[-32:].hex() == "8a5a63503ca163aa238da8e13d2d5662ec7fbd03df6c1eeb10317c83452efee0"
    assert sh[-32:].hex() == "382c3a8f8c92fc2e5a9c2e9db914f59bc4dbf6e8102e091179fe3b821f978b75"
    assert cf[-32:].hex() == "7156cf54bef493f11331f2119980a5d0901c365f38d53bf24162c21c304fd357"
    assert transcript.hex() == "a09adab89f2ad1fce26eb1f21e23e8a1094ddfeb9c4c05cdf83b45ed2752cb53"
    assert [keys["c2s"][purpose].hex() for purpose in ("hdr", "aes", "chacha", "hmac")] == [
        "f23507808fa3b0ce6e3106d9797939e3bbedca8196249eef2df2249f54ecfd8d",
        "436418f1f2ee0db243a50b6c7312b5e68db32a59d0331bce7e014f525b7a1972",
        "95b7791c4eb4e6d88d15617cce92fac26bfc0bcdfd4fae8de0d2a11a530f3b57",
        "207fb0300784e7d80fcafb8685152102b80d448bbf4e4eeb328bd586b4156459",
    ]
    return ch, sh, cf, transcript, keys


def record(keys, frame_type, mode, sequence, item_id, index, body):
    control = frame_type in (1, 5, 6, 7)
    overhead = 32 if control else (0, 16, 16, 32)[mode]
    header = HEADER.pack(1, frame_type, mode, 0, sequence, item_id, index, len(body))
    prefix = struct.pack(">I", 72 + len(body) + overhead)
    routing = prefix + header
    header_mac = mac(keys["hdr"], b"ULAB1-HDR\0" + routing)
    aad = b"ULAB1-PAYLOAD\0" + routing
    nonce = struct.pack(">IQ", 0, sequence)
    if control:
        encoded = body + mac(keys["ctl"], b"ULAB1-CTL\0" + routing + body)
    elif mode == 0:
        encoded = body
    elif mode == 1:
        encoded = AESGCM(keys["aes"]).encrypt(nonce, body, aad)
    elif mode == 2:
        encoded = ChaCha20Poly1305(keys["chacha"]).encrypt(nonce, body, aad)
    else:
        encoded = body + mac(keys["hmac"], aad + body)
    return routing + header_mac + encoded


def exchange(keys, label, origin, mode, body, kind):
    peer = "s2c" if origin == "c2s" else "c2s"
    records = []
    sequences = {"c2s": 0, "s2c": 0}

    def add(direction, frame_type, index, data):
        sequence = sequences[direction]
        sequences[direction] += 1
        wire = record(keys[direction], frame_type, mode, sequence, ITEM_ID, index, data)
        records.append({
            "direction": direction, "type": frame_type, "sequence": sequence,
            "item_id": ITEM_ID.hex(), "index": index, "body": data.hex(), "record": wire.hex(),
        })

    add(origin, 1, 0, b'{"mode":%d}' % mode)
    add(peer, 5, 0, b'{"stage":"ready"}')
    if kind == "text":
        metadata = b'{"kind":"text","name":"","media_type":"text/plain; charset=utf-8","size":%d,"chunk_size":65536}'
    else:
        metadata = b'{"kind":"file","name":"fixture.bin","media_type":"application/octet-stream","size":%d,"chunk_size":65536}'
    add(origin, 2, 0, metadata % len(body))
    chunks = [body[start:start + CHUNK] for start in range(0, len(body), CHUNK)]
    for index, chunk in enumerate(chunks):
        add(origin, 3, index, chunk)
    end = b'{"bytes":%d,"chunks":%d,"sha256":"%s"}'
    add(origin, 4, len(chunks), end % (len(body), len(chunks), sha(body).encode("ascii")))
    add(peer, 5, 0, b'{"stage":"complete"}')
    return {"label": label, "origin": origin, "mode": mode, "records": records}


def corpus():
    assert sha((ROOT / "protocol" / "PROTOCOL_V1.md").read_bytes()) == CONTRACT_HASH
    ch, sh, cf, transcript, keys = handshake()
    body_kats = (
        TEXT.hex(),
        "bb849151471526ecff5220478dd9a5931597bfac524fc3b554dfcb4ee1",
        "53695f5666e7845130fd75d8a289094e2f8a60ef5290d3e3bef04ece72",
        "ec9588eb85952c20555342210af5eaa80d078481d50b54ee0b9a4a76cd2edf79daeb00b78508fe01bbb07bf342",
    )
    for mode, body_kat in enumerate(body_kats):
        wire = record(keys["c2s"], 3, mode, 2, ITEM_ID, 0, TEXT)
        assert wire[76:].hex() == body_kat
        assert int.from_bytes(wire[:4], "big") == (85, 101, 101, 117)[mode]
    exchanges = [
        exchange(keys, label, direction, mode, body, kind)
        for direction in ("c2s", "s2c") for mode in range(4)
        for label, body, kind in (
            ("korean", TEXT, "text"), ("empty", b"", "file"),
            ("binary_zeroes", bytes.fromhex("00ff008000"), "file"),
        )
    ]
    bad_bodies = [
        ("duplicate_key", 1, b'{"mode":0,"mode":0}'),
        ("escaped_duplicate_key", 1, rb'{"mode":0,"\u006dode":0}'),
        ("boolean", 1, b'{"mode":true}'), ("fraction", 1, b'{"mode":1.0}'),
        ("exponent", 1, b'{"mode":1e0}'), ("signed", 1, b'{"mode":-0}'),
        ("leading_zero", 1, b'{"mode":01}'), ("wrong_type", 1, b'{"mode":"1"}'),
        ("extra_key", 1, b'{"mode":0,"extra":1}'),
        ("out_of_range", 1, b'{"mode":4}'),
        ("overflow", 1, b'{"mode":18446744073709551616}'),
        ("invalid_unicode_scalar", 5, rb'{"stage":"ready\ud800"}'),
        ("invalid_utf8", 5, b'{"stage":"ready\xff"}'),
        ("bom", 5, b'\xef\xbb\xbf{"stage":"ready"}'),
        ("nested", 1, b'{"mode":{"value":1}}'),
    ]
    boundary_items = []
    for size in (0, 1, CHUNK - 1, CHUNK, CHUNK + 1):
        body = bytes(index % 251 for index in range(size))
        records = exchange(keys, "boundary", "c2s", 1, body, "file")["records"]
        lengths = [len(bytes.fromhex(row["body"])) for row in records if row["type"] == 3]
        assert sum(lengths) == size and all(length == CHUNK for length in lengths[:-1])
        boundary_items.append({"size": size, "pattern": "byte[i] = i % 251", "sha256": sha(body), "chunks": lengths})
    korean_boundary = b"a" * (CHUNK - 1) + "안".encode("utf-8")
    assert korean_boundary[:CHUNK].endswith(b"\xec") and len(korean_boundary) == CHUNK + 2
    boundary_items.append({
        "size": len(korean_boundary), "pattern": "65535 ASCII a bytes followed by U+C548",
        "sha256": sha(korean_boundary), "chunks": [CHUNK, 2],
    })
    return {
        "description": "PUBLIC TEST DATA. Never use these secrets for live pairing.",
        "revision": REVISION,
        "handshake": {
            "psk": PSK.hex(), "nc": NC.hex(), "ns": NS.hex(), "client_hello": ch.hex(),
            "server_hello": sh.hex(), "client_finish": cf.hex(),
            "transcript_hash": transcript.hex(),
            "keys": {
                direction: {name: value.hex() for name, value in purposes.items()}
                for direction, purposes in keys.items()
            },
        },
        "exchanges": exchanges,
        "negative_bodies": [
            {"label": label, "type": frame_type, "body": value.hex()}
            for label, frame_type, value in bad_bodies
        ],
        "boundary_items": boundary_items,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare the existing fixture to regenerated bytes.")
    args = parser.parse_args()
    path = ROOT / "protocol" / "vectors" / "ulab1.json"
    values = corpus()
    content = (json.dumps(values, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    if args.check:
        if not path.is_file() or path.read_bytes() != content:
            parser.exit(1, f"Fixture mismatch: {path}\n")
    else:
        path.write_bytes(content)
    print(f"{REVISION}: {len(values['exchanges'])} exchanges, "
          f"{len(values['negative_bodies'])} malformed bodies, {len(values['boundary_items'])} boundary cases.")


if __name__ == "__main__":
    main()
