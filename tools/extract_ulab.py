"""Stream visible ULAB content from a lab PCAP/PCAPNG. Requires TShark with Lua."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


LUA = Path(__file__).resolve().parents[1] / "dissectors/ulab.lua"
CHUNK = 65536
MAX_FILE = 1 << 30


def pairs(values):
    result = {}
    for k, v in values:
        if k in result:
            raise ValueError("Duplicate JSON member")
        result[k] = v
    return result


def body_json(data, keys):
    obj = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    if not isinstance(obj, dict) or obj.keys() != set(keys):
        raise ValueError("Unexpected JSON schema")
    return obj


def uint(value, limit):
    return type(value) is int and 0 <= value <= limit


@dataclass
class Item:
    stream: int
    direction: str
    item_id: str
    mode: int
    kind: str | None = None
    media_type: str | None = None
    size: int | None = None
    meta_seen: bool = False
    chunks: int = 0
    total: int = 0
    sha: object = field(default_factory=hashlib.sha256)
    temp: object = None

    def report(self, status, **extras):
        return dict(stream=self.stream, direction=self.direction, item_id=self.item_id,
                    mode=self.mode, kind=self.kind, status=status,
                    authentication="unverified", **extras)

    def cleanup(self):
        if self.temp is not None:
            self.temp.close()
            Path(self.temp.name).unlink(missing_ok=True)


@dataclass
class Coverage:
    """TCP sequence space covered by capture versus bytes decoded in full records."""
    next_raw: int
    expected: int = 0
    parsed: int = 0

    def observe(self, raw, size):
        if not size:
            return
        end = (raw + size) & 0xffffffff
        advance = (end - self.next_raw) & 0xffffffff
        if 0 < advance < (1 << 31):
            self.next_raw = end
            self.expected += advance


class Extraction:
    def __init__(self, output, budget):
        self.output = output
        self.budget = budget
        self.active = {}
        self.coverage = {}
        self.sequences = {}
        self.seen = {}
        self.rejected = set()
        self.results = 0
        self.incomplete = False

    def emit(self, record):
        print(json.dumps(record, ensure_ascii=True), flush=True)
        self.results += 1
        if record["status"] == "incomplete":
            self.incomplete = True

    def fail(self, stream, reason):
        if stream in self.rejected:
            return
        self.rejected.add(stream)
        matching = [key for key in self.active if key[0] == stream]
        if not matching:
            self.emit({"stream": stream, "status": "incomplete", "reason": reason})
        for key in matching:
            item = self.active.pop(key)
            item.cleanup()
            self.emit(item.report("incomplete", reason=reason))

    def record(self, stream, fields):
        if stream in self.rejected:
            return
        if "invalid" in fields:
            self.fail(stream, fields["invalid"].get("show"))
            return
        if "frame_type" not in fields:
            return
        direction = fields["direction"].get("show")
        key = (stream, direction)
        mode = int(fields["mode"].get("show"))
        seq = int(fields["sequence"].get("show"))
        index = int(fields["chunk_index"].get("show"))
        item_id = fields["item_id"].get("value")
        typ = fields["record"].get("show")
        data = bytes.fromhex(fields["plaintext"].get("value", "")) if "plaintext" in fields else None
        if seq != self.sequences.get(key, 0):
            raise ValueError("Missing or repeated sender sequence")
        self.sequences[key] = seq+1
        if typ in ("ACK", "DRAIN"):
            return
        if typ == "ERROR":
            self.fail(stream, "Endpoint sent ERROR")
            return
        if typ == "OPEN":
            seen = self.seen.setdefault(key, set())
            opened = body_json(data, ["mode"])["mode"]
            if (key in self.active or item_id in seen or len(seen) >= 4096
                    or not uint(opened, 3) or opened != mode):
                raise ValueError("Invalid OPEN or repeated item")
            if len(self.active) >= 64:
                raise ValueError("Too many concurrent items")
            seen.add(item_id)
            self.active[key] = Item(stream, direction, item_id, mode)
            return
        item = self.active.get(key)
        if not item or item.item_id != item_id or item.mode != mode:
            raise ValueError("Missing OPEN or inconsistent item/mode")
        if mode in (1, 2):
            if typ == "META":
                if item.meta_seen:
                    raise ValueError("Repeated encrypted META")
                item.meta_seen = True
            elif not item.meta_seen or index != item.chunks:
                raise ValueError("Missing encrypted META or chunk")
            elif typ == "DATA":
                item.chunks += 1
            elif typ == "END":
                self.emit(item.report("encrypted", reason="Endpoint keys unavailable"))
                del self.active[key]
            return
        if typ == "META":
            meta = body_json(data, ["kind", "name", "media_type", "size", "chunk_size"])
            kind, media = meta["kind"], meta["media_type"]
            if (item.size is not None or kind not in ("text", "image", "file")
                    or not isinstance(meta["name"], str) or not isinstance(media, str)
                    or not uint(meta["size"], (1 << 20) if kind == "text" else MAX_FILE)
                    or type(meta["chunk_size"]) is not int or meta["chunk_size"] != CHUNK
                    or (kind == "text" and (meta["name"] or media != "text/plain; charset=utf-8"))
                    or (kind != "text" and not (0 < len(meta["name"].encode("utf-8")) <= 255))
                    or not re.fullmatch(r"[\x20-\x7e]{1,127}", media)
                    or (kind == "image" and not media.startswith("image/"))):
                raise ValueError("Invalid META")
            item.kind, item.media_type, item.size = kind, media, meta["size"]
            if item.size > self.budget:
                raise ValueError("Extraction budget exceeded")
            self.budget -= item.size
            item.temp = tempfile.NamedTemporaryFile(dir=self.output, suffix=".part", delete=False)
            return
        if item.size is None or index != item.chunks:
            raise ValueError("Missing META or out-of-order chunk")
        if typ == "DATA":
            if len(data) != min(CHUNK, item.size - item.total) or not data:
                raise ValueError("Chunk length disagrees with META")
            item.temp.write(data)
            item.sha.update(data)
            item.chunks += 1
            item.total += len(data)
            return
        end = body_json(data, ["bytes", "chunks", "sha256"])
        digest = item.sha.hexdigest()
        if (not uint(end["bytes"], MAX_FILE) or not uint(end["chunks"], MAX_FILE // CHUNK)
                or item.total != item.size or end["bytes"] != item.total
                or end["chunks"] != item.chunks or end["sha256"] != digest):
            raise ValueError("Visible END digest, length or chunk count disagrees")
        item.temp.close()
        staged = Path(item.temp.name)
        suffix = ".txt" if item.kind == "text" else ".bin"
        if item.media_type == "image/png":
            with staged.open("rb") as fp:
                if fp.read(8) == b"\x89PNG\r\n\x1a\n":
                    suffix = ".png"
        # Never use the remote filename, and never overwrite an existing file.
        dest = self.output / f"stream-{stream}-{direction}-{item_id}{suffix}"
        if os.name == "nt":
            os.rename(staged, dest)  # Windows fails when the target already exists.
        else:
            os.link(staged, dest)
        item.cleanup()
        del self.active[key]
        self.emit(item.report("extracted", file=dest.name, bytes=item.total, sha256=digest))

    def packet(self, packet, ports):
        tcp = packet.find(".//proto[@name='tcp']")
        if tcp is None:
            return

        def tcp_value(name):
            return tcp.find(f".//field[@name='tcp.{name}']").get("show")

        stream = int(tcp_value("stream"))
        direction = "s2c" if int(tcp_value("srcport")) in ports else "c2s"
        syn = tcp.find(".//field[@name='tcp.flags.syn']").get("value") == "1"
        raw = (int(tcp_value("seq_raw")) + int(syn)) & 0xffffffff
        key = (stream, direction)
        size = int(tcp_value("len"))
        if syn or size:
            self.coverage.setdefault(key, Coverage(raw)).observe(raw, size)
        for proto in packet.findall(".//proto[@name='ulab']"):
            fields = {f.get("name")[5:]: f for f in proto.findall("field")
                      if f.get("name", "").startswith("ulab.")}
            if "record" in fields and key in self.coverage:
                self.coverage[key].parsed += int(proto.get("size"))
            try:
                self.record(stream, fields)
            except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
                self.fail(stream, str(exc) if type(exc) is ValueError else "Invalid visible body")

    def finish(self):
        for (stream, direction), coverage in self.coverage.items():
            if coverage.expected != coverage.parsed:
                self.fail(stream, f"Incomplete TCP record or gap in {direction}")
        for item in self.active.values():
            item.cleanup()
            self.emit(item.report("incomplete", reason="Capture ended before END"))
        self.active.clear()
        if not self.results:
            self.emit({"status": "incomplete", "reason": "No complete ULAB items observed"})


def run(args):
    if not args.capture.is_file():
        raise ValueError("Input capture not found")
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError("Choose a new or empty output directory")
    session = Extraction(args.out, args.max_bytes)
    cmd = [args.tshark, "-n", "-X", "lua_script:" + str(LUA),
           "-o", "tcp.desegment_tcp_streams:TRUE", "-o", "tcp.reassemble_out_of_order:TRUE",
           "-o", "ulab.port:" + str(args.port), "-o", "ulab.iproxy_port:" + str(args.iproxy_port),
           "-r", str(args.capture), "-Y", f"tcp.port == {args.port} || tcp.port == {args.iproxy_port}",
           "-T", "pdml", "-J", "tcp ulab"]
    # Keep stderr attached for diagnostic output. Drain the child's XML per packet.
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        try:
            events = ET.iterparse(proc.stdout, events=("start", "end"))
            _, root = next(events)
            for event, element in events:
                if event != "end" or element.tag != "packet":
                    continue
                session.packet(element, (args.port, args.iproxy_port))
                root.remove(element)
                element.clear()
            if proc.wait() != 0:
                raise ValueError("TShark failed while reading capture")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            session.finish()
    return int(session.incomplete)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="A new or empty output directory")
    parser.add_argument("--tshark", default="tshark")
    parser.add_argument("--port", type=int, default=43123)
    parser.add_argument("--iproxy-port", type=int, default=43124)
    parser.add_argument("--max-bytes", type=int, default=MAX_FILE, help="Total staged bytes allowed")
    args = parser.parse_args()
    if not all(1 <= p <= 65535 for p in (args.port, args.iproxy_port)) or args.max_bytes < 0:
        parser.error("Check port range (1–65535) and nonnegative extraction budget")
    try:
        return run(args)
    except (OSError, ValueError, ET.ParseError, StopIteration) as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
