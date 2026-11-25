"""Exercise the actual TShark Lua plugin with PUBLIC synthetic protocol vectors."""
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "protocol/vectors"
LUA = ROOT / "dissectors/ulab.lua"
EXTRACT = ROOT / "tools/extract_ulab.py"
TSHARK = shutil.which("tshark")
PNG_HASH = "83b264166861967a51a889bc26b8156364d9079c96e900b617e5c54f7431bfb2"


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(x[0] for x in struct.iter_unpack("!H", data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return ~total & 65535


class SyntheticPcap:
    """TCP over raw IPv4, Ethernet-free. Only RFC 5737 addresses and public fixtures."""
    def __init__(self, fp):
        self.fp = fp
        fp.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 262144, 101))
        self.count = 0

    def packet(self, direction, port, seq, ack, flags, data):
        a, b = (socket.inet_aton(x) for x in ("192.0.2.1", "192.0.2.2"))
        src, dst = port, 43123
        if direction == "s2c":
            a, b, src, dst = b, a, dst, src
        tcp = struct.pack("!HHIIBBHHH", src, dst, seq, ack, 80, flags, 65535, 0, 0)
        cs = checksum(a + b + struct.pack("!BBH", 0, 6, 20 + len(data)) + tcp + data)
        tcp = tcp[:16] + struct.pack("!H", cs) + tcp[18:]
        ip = struct.pack("!BBHHHBBH4s4s", 69, 0, 40+len(data), 1, 0, 64, 6, 0, a, b)
        pkt = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:] + tcp + data
        self.count += 1
        self.fp.write(struct.pack("<IIII", 1_700_000_000 + self.count // 1000,
                                  self.count % 1000 * 1000, len(pkt), len(pkt)) + pkt)

    def session(self, port, records):
        seq = {"c2s": 100, "s2c": 700}
        self.packet("c2s", port, 99, 0, 2, b"")
        self.packet("s2c", port, 699, 100, 18, b"")
        self.packet("c2s", port, 100, 700, 16, b"")
        for send, grouped in itertools.groupby(records, key=lambda x: x[0]):
            other = "s2c" if send == "c2s" else "c2s"
            data = b"".join(record for _, record in grouped)
            # Split magic/length, then fixed headers. Ship multiple records together.
            cuts = sorted({0, min(2, len(data)), min(13, len(data)), len(data)})
            for l, r in zip(cuts, cuts[1:]):
                for off in range(l, r, 15_000):
                    part = data[off:min(r, off+15_000)]
                    self.packet(send, port, seq[send], seq[other], 24, part)
                    if off == 0:  # retransmitted bytes must not duplicate logical frames
                        self.packet(send, port, seq[send], seq[other], 24, part)
                    seq[send] += len(part)
        self.packet("c2s", port, seq["c2s"], seq["s2c"], 17, b"")


def fixture(path, error=None, extended=False):
    vectors = json.loads((VECTORS / "v1-positive.json").read_bytes())
    pairing = json.loads((VECTORS / "pairing-v1.json").read_bytes())
    enroll = [bytes.fromhex(v) for v in pairing["record_hex_in_order"]]
    for k, who in [(5, "C"), (6, "S")]:
        enroll.append(b"ULABP001"+bytes([k, 0, 32])+bytes.fromhex(pairing["confirmation_hex"][who]))
    hello = [(d, bytes.fromhex(vectors["handshake_hex"][k])) for d, k in
             [("c2s", "client_hello"), ("s2c", "server_hello"), ("c2s", "client_finish")]]
    with path.open("wb") as fp:
        trace = SyntheticPcap(fp)
        scenarios = vectors["scenarios"][:6 if extended else 4]
        for i, sc in enumerate(scenarios if not error else scenarios[:1]):
            rows = [(r["direction"], bytes.fromhex(r["wire_hex"])) for r in sc["records"]]
            if error == "length":
                rows = [("c2s", b"\xff\xff\xff\xff" + b"ULAB0001" + b"x"*80)]
            elif error == "midstream":
                hello = []
                rows = [("c2s", b"junk bytes ULAB0001" + b"x"*80)]
            elif error == "cut":
                rows = rows[:3] + [(rows[3][0], rows[3][1][:30])]
            elif error == "trailing":
                rows.append(("c2s", b"\x00\x00"))
            elif error == "bad_hash":
                d, rec = rows[3]
                rows[3] = (d, rec[:-1] + bytes([rec[-1] ^ 1]))
            elif error == "bad_header":
                d, rec = rows[3]
                rows[3] = (d, rec[:7] + b"\xff" + rec[8:])
            elif error == "missing_chunk":
                rows.pop(3)
            beginning = [(("c2s" if j % 2 == 0 else "s2c"), v)
                         for j, v in enumerate(enroll)] if i == 3 else []
            trace.session(51000+i, beginning + hello + rows)


class DissectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(TSHARK, "Install TShark with Lua for the dissector suite")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"synthetic.pcap"

    def fields(self, display_filter, *names):
        cmd = [TSHARK, "-n", "-2", "-X", "lua_script:"+str(LUA),
               "-r", str(self.path), "-Y", display_filter, "-T", "fields"]
        for name in names:
            cmd.extend(["-e", name])
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Lua Error", result.stderr)
        return result.stdout.strip().splitlines()

    def test_modes_reassembly_and_tags(self):
        fixture(self.path)
        for m in range(4):
            values = self.fields(f"ulab.mode == {m}", "ulab.mode")
            self.assertEqual(sum(row.split(",").count(str(m)) for row in values), 12)
        self.assertTrue(self.fields("ulab.aead_tag && ulab.ciphertext", "ulab.aead_tag"))
        self.assertTrue(self.fields("ulab.data_hmac && ulab.plaintext", "ulab.data_hmac"))
        self.assertEqual(self.fields("ulab.pair_stage", "ulab.pair_stage"),
                         [str(i) for i in range(1, 7)])
        self.assertEqual(len(self.fields("ulab.handshake_type == 1", "tcp.stream")), 4)

    def test_unfiltered_two_pass_and_repeat_plugin(self):
        fixture(self.path)
        cmd = [TSHARK, "-n", "-2", "-X", "lua_script:"+str(LUA),
               "-X", "lua_script:"+str(LUA), "-r", str(self.path),
               "-T", "fields", "-e", "ulab.record"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("DATA"), 8)
        self.assertNotIn("Error", proc.stderr)

    def test_empty_files_chunk_boundaries_and_concurrent_items(self):
        fixture(self.path, extended=True)
        out = Path(self.tmp.name)/"large"
        rows, code = self.extract(out)
        self.assertEqual(code, 0)
        recovered = [r for r in rows if r["status"] == "extracted" and r["stream"] == 4]
        self.assertEqual(sorted(r["bytes"] for r in recovered), [0, 65536, 65537, 65548])
        self.assertEqual(sum(r["status"] == "extracted" and r["stream"] == 5 for r in rows), 2)

    def test_malformed_and_midstream_do_not_scan(self):
        for err in ("length", "bad_header", "midstream"):
            with self.subTest(err=err):
                fixture(self.path, err)
                self.assertTrue(self.fields("ulab.invalid", "frame.number"))
                got = self.fields("ulab.handshake_type == 1", "frame.number")
                self.assertEqual(len(got), 0 if err == "midstream" else 1)

    def extract(self, output):
        cmd = [sys.executable, str(EXTRACT), str(self.path), "--out", str(output)]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertIn(result.returncode, [0, 1], result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return [json.loads(l) for l in result.stdout.splitlines()], result.returncode

    def test_extract_visible_png_and_text_without_keys(self):
        fixture(self.path)
        out = Path(self.tmp.name)/"recovered"
        reports, code = self.extract(out)
        self.assertEqual(code, 0)
        items = [r for r in reports if r["status"] == "extracted"]
        self.assertEqual(len(items), 4)
        self.assertEqual({(r["mode"], r["direction"], r["kind"]) for r in items},
                         {(m, d, k) for m in [0,3] for d,k in [("s2c","image"),("c2s","text")]})
        for r in items:
            data = (out/r["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), r["sha256"])
            if r["kind"] == "image":
                self.assertEqual(r["sha256"], PNG_HASH)
            else:
                self.assertEqual(data, "안녕, USB!\n".encode())
            self.assertEqual(r["authentication"], "unverified")
        self.assertEqual(sum(r["status"] == "encrypted" for r in reports), 4)

    def test_capture_trailing_partial_header_reported_after_complete_items(self):
        fixture(self.path, "trailing")
        results, code = self.extract(Path(self.tmp.name)/"trailing")
        self.assertEqual(code, 1)
        self.assertTrue(any(r["status"] == "incomplete" for r in results))
        self.assertEqual(sum(r["status"] == "extracted" for r in results), 2)

    def test_incomplete_or_inconsistent_never_exported(self):
        for err in ("cut", "bad_hash", "missing_chunk"):
            with self.subTest(err=err):
                fixture(self.path, err)
                out = Path(self.tmp.name)/err
                results, code = self.extract(out)
                self.assertEqual(code, 1)
                self.assertTrue(any(r["status"] == "incomplete" for r in results))
                self.assertFalse(any(r.get("kind") == "text" and r["status"] == "extracted" for r in results))
                self.assertFalse(list(out.glob("*.part")))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write-synthetic":
        fixture(Path(sys.argv[2]))
    else:
        unittest.main()
