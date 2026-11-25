#!/usr/bin/env python3
"""Run Python ULAB endpoints through the production relay core and capture Linux loopback."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zlib


ROOT = Path(__file__).resolve().parents[1]
LO = "127.0.0.1"
LUA = ROOT / "dissectors/ulab.lua"


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def png():
    """Reproducible RGB pattern with real PNG framing, no imaging dependency."""
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name+data))
    w, h = 96, 64
    pixels = b"".join(b"\0" + bytes((x*u + y*v) % 256 for x in range(w)
                                     for u, v in [(5, 11), (23, 3), (7, 17)]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


async def relay_worker(upstream, status):
    # Separate process. Its only inputs are ports and its public status path.
    sys.path.insert(0, str(ROOT / "apps/raspberrypi"))
    from relay import Relay, Settings
    sock = socket.socket()
    sock.bind((LO, 0))
    sock.setblocking(False)
    sock.listen()
    port = sock.getsockname()[1]
    # Lab-only listener bypasses hardware admission. The production entry point
    # retains gadget binding, peer restrictions and iproxy owner validation.
    instance = Relay(Settings("lo", LO, LO, port, upstream), status)
    async with await asyncio.start_server(instance.accept, sock=sock):
        print(json.dumps({"ready": port}), flush=True)
        try:
            await asyncio.to_thread(sys.stdin.buffer.readline)
            await asyncio.wait_for(instance.wait_idle(), 5)
        finally:
            await instance.stop()


async def start_capture(path, front, upstream):
    expr = f"tcp and host {LO} and (port {front} or port {upstream})"
    proc = await asyncio.create_subprocess_exec("dumpcap", "-i", "lo", "-f", expr,
                "-s", "262144", "-a", "duration:30", "-a", "filesize:4096",
                "-w", str(path), stderr=asyncio.subprocess.PIPE,
                env=dict(os.environ, LC_ALL="C"))
    messages = bytearray()
    try:
        async with asyncio.timeout(10):
            while raw := await proc.stderr.readline():
                messages.extend(raw)
                if raw.lstrip().startswith(b"File:"):
                    return proc, messages, expr
        raise RuntimeError("dumpcap failed to start: " + messages.decode(errors="replace"))
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise


async def stop_capture(proc, messages, path):
    # Linux libpcap may hold the final burst in its kernel ring until the
    # block-retirement timeout. Immediate SIGINT can produce a zero-packet trace.
    await asyncio.sleep(1.1)
    if proc.returncode is None:
        proc.send_signal(signal.SIGINT)
    try:
        _, rest = await asyncio.wait_for(proc.communicate(), 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    messages.extend(rest or b"")
    path.write_bytes(messages)
    if proc.returncode != 0:
        raise RuntimeError(f"dumpcap failed; see {path.name}")


def capture_stats(path, front, upstream, needle):
    cmd = ["tshark", "-n", "-X", "lua_script:"+str(LUA),
           "-o", f"ulab.port:{front}", "-o", f"ulab.iproxy_port:{upstream}",
           "-r", str(path), "-T", "pdml", "-J", "tcp ulab"]
    packets = ET.fromstring(subprocess.check_output(cmd)).findall("packet")
    legs = {"front": Counter(), "upstream": Counter()}
    content = {}
    lengths = {}
    for packet in packets:
        tcp = packet.find(".//proto[@name='tcp']")
        if tcp is None:
            raise RuntimeError("Unexpected non-TCP packet in scoped capture")
        ports = {int(tcp.find(f"field[@name='tcp.{f}port']").get("show")) for f in ("src", "dst")}
        leg = "front" if front in ports else "upstream" if upstream in ports else None
        if not leg:
            raise RuntimeError("Unexpected port outside capture filter")
        for proto in packet.findall(".//proto[@name='ulab']"):
            fields = {f.get("name", "")[5:]: f for f in proto.findall("field")}
            if "invalid" in fields:
                raise RuntimeError("Malformed real ULAB capture")
            name = fields["record"].get("show")
            legs[leg][name] += 1
            if name == "DATA":
                key = (leg, fields["direction"].get("show"))
                field = fields["plaintext"] if "plaintext" in fields else fields["ciphertext"]
                content.setdefault(key, bytearray()).extend(bytes.fromhex(field.get("value")))
                lengths[key] = lengths.get(key, 0) + int(proto.get("size"))
    if set(content) != {(leg, d) for leg in legs for d in ("c2s", "s2c")}:
        raise RuntimeError("Missing DATA on a direction or leg")
    for d in ("c2s", "s2c"):
        if content["front", d] != content["upstream", d]:
            raise RuntimeError("Forwarded DATA bytes differ between capture legs")
    seen = {needle in data for data in content.values()}
    if len(seen) != 1:
        raise RuntimeError("Inconsistent plaintext marker visibility between paths")
    return (len(packets), next(iter(seen)), legs,
            {f"{leg}_{d}": n for (leg, d), n in lengths.items()})


async def scenario(out, mode, kind, marker):
    from usblinklab.protocol import Mode
    from usblinklab.session import Session
    from usblinklab.storage import Storage

    directory = out / f"{int(mode)}-{kind}"
    directory.mkdir()
    capture_path = directory / "loopback.pcapng"
    expected = (out / ("source.png" if kind == "image" else "source.txt")).read_bytes()
    ready = asyncio.get_running_loop().create_future()
    def accept(reader, writer):
        if ready.done():
            writer.close()
        else:
            ready.set_result((reader, writer))
    listener = await asyncio.start_server(accept, LO, 0)
    upstream = listener.sockets[0].getsockname()[1]
    worker = None
    capture = None
    tasks, sessions = [], []
    start_utc = utc()
    endpoint_log = []
    def logger(role):
        def log(ev):
            if ev.status == "complete":
                endpoint_log.append({"utc": utc(), "endpoint": role, "direction": ev.direction,
                                     "status": ev.status, "item_id": ev.item_id.hex(),
                                     "bytes": ev.size, "mode": int(ev.mode), "kind": ev.kind})
        return log
    with tempfile.TemporaryDirectory() as staging:
        stores = [Storage(Path(staging)/role) for role in ("client", "server")]
        try:
            worker = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()),
                          "--relay-worker", str(upstream), str(directory/"relay-status.json"),
                          stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            line = await asyncio.wait_for(worker.stdout.readline(), 10)
            if not line:
                raise RuntimeError("Relay child exited before listen")
            front = json.loads(line)["ready"]
            capture, messages, expr = await start_capture(capture_path, front, upstream)
            client = await asyncio.open_connection(LO, front)
            server = await asyncio.wait_for(ready, 5)
            listener.close()
            # Generated only after starting the independent relay process.
            psk = secrets.token_bytes(32)
            sessions = [Session(*client, psk, stores[0], logger("client"), client=True),
                        Session(*server, psk, stores[1], logger("server"), client=False)]
            tasks = [asyncio.create_task(s.run()) for s in sessions]
            await asyncio.gather(*(s.wait_ready() for s in sessions))
            jobs = [(s.stage_file(out/"source.png", mode, kind="image", media_type="image/png")
                     if kind == "image" else s.stage_text(marker, mode)) for s in stores]
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
            await asyncio.gather(*(s.send(j) for s, j in zip(sessions, jobs)))
            wall_ms = (time.perf_counter_ns()-wall)/1e6
            cpu_ms = (time.process_time_ns()-cpu)/1e6
            hashes = {}
            for side, storage in zip(("client", "server"), stores):
                item, = storage.received()
                original = item.path.read_bytes()
                hashes[side] = digest(original)
                if item.mode != mode or original != expected or item.sha256 != hashes[side]:
                    raise RuntimeError("Persisted endpoint bytes differ from source")
            if len(endpoint_log) != 4:
                raise RuntimeError("Missing endpoint completion events")
        finally:
            listener.close()
            if sessions:
                await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if worker:
                    try:
                        stdout, _ = await asyncio.wait_for(worker.communicate(b"\n"), 8)
                    except TimeoutError:
                        worker.kill()
                        await worker.wait()
                        raise
                    (directory / "relay.jsonl").write_bytes(stdout)
                    if worker.returncode:
                        raise RuntimeError("Test relay failed")
            finally:
                if capture:
                    await stop_capture(capture, messages, directory/"dumpcap.log")
            await asyncio.wait_for(listener.wait_closed(), 3)
    # PNG signature or the same unique text marker, checked only in DATA bodies.
    needle = expected[:8] if kind == "image" else expected
    packets, visible, legs, wire = capture_stats(capture_path, front, upstream, needle)
    if visible != (mode in (Mode.PLAINTEXT, Mode.HMACSHA256)):
        raise RuntimeError("Unexpected application DATA visibility")
    extracted = subprocess.check_output([sys.executable, str(ROOT/"tools/extract_ulab.py"),
                  str(capture_path), "--out", str(directory/"extracted"), "--port", str(front),
                  "--iproxy-port", str(upstream)])
    (directory/"extraction.jsonl").write_bytes(extracted)
    rows = [json.loads(row) for row in extracted.splitlines()]
    if len(rows) != 4:
        raise RuntimeError("Expected two directions on each of two captured legs")
    for row in rows:
        if visible:
            if row["status"] != "extracted" or (directory/"extracted"/row["file"]).read_bytes() != expected:
                raise RuntimeError("Recovered bytes differ from independent source")
        elif row["status"] != "encrypted":
            raise RuntimeError("Unexpected encrypted extraction result")
    (directory/"endpoints.jsonl").write_text("".join(json.dumps(r)+"\n" for r in endpoint_log), encoding="utf-8")
    return dict(mode=int(mode), mode_name=mode.name, kind=kind, utc_start=start_utc, utc_end=utc(),
                capture=str(capture_path.relative_to(out)), interface="lo", bpf=expr,
                capture_sha256=digest(capture_path.read_bytes()), capture_size=capture_path.stat().st_size,
                captured_packets=packets, legs=legs, data_record_bytes=wire, payload_bytes=len(expected),
                source_sha256=digest(expected), received_sha256=hashes, visible_in_data=visible,
                recovered_files=sum(r["status"]=="extracted" for r in rows), delivery="complete ACK both directions",
                authentication=("header/control only" if mode==0 else "endpoint authenticated body"),
                bidirectional_completion_ms=wall_ms, endpoint_pair_process_cpu_ms=cpu_ms)


async def experiment(args):
    import cryptography
    from usblinklab.protocol import Mode
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Choose a new or empty experiment output directory")
    marker = "ULAB-LAB-" + secrets.token_hex(16)
    (out/"source.txt").write_text(marker, encoding="utf-8")
    (out/"source.png").write_bytes(png())
    sources = ["tools/relay_experiment.py", "tools/extract_ulab.py", "dissectors/ulab.lua",
               "apps/raspberrypi/relay.py", "apps/windows/usblinklab/protocol.py",
               "apps/windows/usblinklab/session.py", "apps/windows/usblinklab/storage.py"]
    report = {"transport":"Linux loopback TCP", "endpoints":"Two Python Session roles",
              "source_sha256": {p:digest((ROOT/p).read_bytes()) for p in sources},
              "python":sys.version.split()[0], "cryptography":cryptography.__version__,
              "kernel":os.uname().release, "ci_commit":os.environ.get("GITHUB_SHA"),
              "tshark":subprocess.check_output(["tshark","--version"], text=True).splitlines()[0],
              "dumpcap":subprocess.check_output(["dumpcap","--version"], text=True).splitlines()[0],
              "experiments":[]}
    for mode in dict.fromkeys(args.mode if args.mode is not None else range(4)):
        for kind in dict.fromkeys(args.kind or ["text", "image"]):
            case = await asyncio.wait_for(scenario(out, Mode(mode), kind, marker), 45)
            report["experiments"].append(case)
            save_json(out/"comparison.json", report)
            print(f"{mode} {kind}: verified {case['captured_packets']} captured packets", flush=True)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--relay-worker":
        asyncio.run(relay_worker(int(sys.argv[2]), Path(sys.argv[3])))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", type=int, choices=range(4), action="append")
    parser.add_argument("--kind", choices=["text","image"], action="append")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Use Linux with capture permission for dumpcap on lo")
    sys.path.insert(0,str(ROOT/"apps/windows"))
    asyncio.run(experiment(args))


if __name__ == "__main__":
    main()
