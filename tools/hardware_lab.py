#!/usr/bin/env python3
"""Send only generated lab content through the Windows production Pi transport."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/windows"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ReceiveAudit:
    def __init__(self, known_hashes, emit, private_receipt):
        self.known_hashes = known_hashes
        self.emit = emit
        self.private_receipt = private_receipt
        self.seen = set()
        self.generated = set()
        self.received = asyncio.Event()

    def capture_safe(self):
        return self.seen == self.generated

    def observe(self, item):
        if item.direction != "incoming":
            return
        self.seen.add(item.item_id)
        if item.status != "complete" or not item.path:
            return
        # The normal receiving endpoint already validated END and persisted a
        # digest. Do not load original names, text or image bytes into the log.
        record = json.loads((item.path.parent / "metadata.json").read_bytes())
        value = record["sha256"]
        if value in self.known_hashes:
            self.generated.add(item.item_id)
            self.emit("return_committed", mode=item.mode.name, kind=item.kind, bytes=item.size,
                      sha256=value, item_id=item.item_id.hex())
        elif self.private_receipt and re.fullmatch("[a-f0-9]{64}", value):
            self.emit("private_return_committed", mode=item.mode.name, kind=item.kind,
                      bytes=item.size, sha256=value)
        else:
            self.emit("unmatched_return_preserved")
            return
        self.received.set()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class DashboardCapture:
    """Use an already authenticated SSH forward to the Pi dashboard, for this run only."""
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.http = build_opener(ProxyHandler({}), NoRedirect())
        self.token = None
        self.sessions = {}

    def request(self, path, payload=None):
        headers = {"Host": "127.0.0.1:8765"}
        body = None
        if payload is not None:
            headers.update({"Content-Type": "application/json", "X-ULAB-Token": self.token})
            body = json.dumps(payload).encode()
        req = Request(self.base + path, data=body, headers=headers)
        with self.http.open(req, timeout=10) as response:
            return json.load(response)

    def start(self, emit):
        self.token = self.request("/api/status")["token"]
        for layer in ("gadget", "loopback"):
            reply = self.request("/api/start", {"layer":layer, "seconds":120, "mib":8,
                                                "filter":"", "private":False})
            sid = reply["value"]
            if not reply.get("ok") or not re.fullmatch("[a-f0-9]{32}",sid):
                raise ValueError("Capture session was not created")
            self.sessions[layer] = sid
            emit("capture_started", layer=layer, session=sid)

    def finish(self, output, emit, download=True):
        # Allow the kernel packet ring to retire its last partially full block.
        time.sleep(1.2)
        errors = []
        for layer, sid in self.sessions.items():
            try:
                self.request("/api/stop", {"id":sid})
            except HTTPError:
                # Duration limit may already have closed the capture.
                pass
            if not download:
                emit("capture_kept_private_on_pi", layer=layer)
                continue
            try:
                for _ in range(15):
                    rows = self.request("/api/status")["capture"]["captures"]
                    record = next((row for row in rows if row["id"] == sid), {})
                    if record.get("result") != "capturing":
                        break
                    time.sleep(.4)
                if record.get("result") != "complete":
                    raise ValueError("Capture did not complete")
                req = Request(self.base+"/download/"+sid, headers={"Host":"127.0.0.1:8765"})
                with self.http.open(req,timeout=15) as response:
                    raw = response.read(9*1024*1024)
                if (len(raw) != record["size"] or raw[:4] != bytes.fromhex("0a0d0d0a")
                        or digest(raw) != record.get("sha256")):
                    raise ValueError("Capture size, signature or origin SHA-256 mismatch")
                (output/(layer+".pcapng")).write_bytes(raw)
                (output/(layer+".json")).write_text(json.dumps(record, indent=2)+"\n",encoding="utf-8")
                emit("capture_saved", layer=layer, size=len(raw), sha256=digest(raw),
                     packets=record.get("packets"), dropped=record.get("dropped"))
            except (OSError, KeyError, ValueError, TypeError) as exc:
                errors.append(type(exc).__name__)
                emit("capture_unavailable", layer=layer, reason=type(exc).__name__)
        return not errors


async def run(args):
    from usblinklab.pi_transport import PathSettings
    from usblinklab.protocol import Mode
    from usblinklab.session import Session
    from usblinklab.storage import Storage
    from relay_experiment import png
    normal_root = Path(os.environ["LOCALAPPDATA"]) / "USBLinkLab"
    cfg = PathSettings("relay", args.pi, args.port, args.reuse_direct)
    provider = cfg.provider(normal_root)
    args.output.mkdir(parents=True, exist_ok=False)
    log = args.output / "endpoint.jsonl"

    def emit(event, **fields):
        row = {"utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "event": event, **fields}
        with log.open("a", encoding="utf-8") as f:
            print(json.dumps(row, ensure_ascii=True), file=f)
        print(json.dumps(row, ensure_ascii=True), flush=True)

    marker = "ULAB-" + args.tag + "-SYNTHETIC"
    blob = bytes(range(256)) * 259
    generated = {"text": marker.encode(), "image": png(), "file": blob}
    return_hash = digest(b"ULAB-RETURN-01")
    accepted = {digest(data) for data in generated.values()} | {return_hash}
    audit = ReceiveAudit(accepted, emit, args.private_receipt)
    task = session = None
    phase = "route"
    capture = DashboardCapture(args.capture_dashboard_port) if args.capture_dashboard_port else None
    captured = True
    clean = False
    try:
        sources = ["tools/hardware_lab.py", "tools/relay_experiment.py",
                   "apps/windows/usblinklab/pi_transport.py", "apps/windows/usblinklab/session.py",
                   "apps/windows/usblinklab/protocol.py", "apps/windows/usblinklab/storage.py"]
        emit("physical_run", direction="windows_via_pi", mode=args.mode, kind=args.kind,
             sources_sha256={path:digest((ROOT/path).read_bytes()) for path in sources})
        device, = await provider.discover()
        link = provider.selection[1]
        emit("gadget_route_verified", index=link.index, local_ip=link.source,
             pi=cfg.destination, port=cfg.port)
        pairing = provider.pairings.load(device.stable_id)
        if pairing is None:
            emit("saved_pair_missing")
            return 2
        if capture:
            phase = "capture_start"
            await asyncio.to_thread(capture.start, emit)
        phase = "connect"
        with tempfile.TemporaryDirectory(prefix="ulab-real-endpoint-") as scratch:
            temp = Path(scratch)
            store = Storage(temp / "data")
            for kind, name in (("image", "ULAB-LAB.png"), ("file", "ULAB-LAB.bin")):
                (temp / name).write_bytes(generated[kind])

            receiving = Storage(normal_root / "storage")
            reader, writer = await provider.connect(device)
            emit("gadget_connected", local_ip=writer.get_extra_info("sockname")[0],
                 pi=writer.get_extra_info("peername")[0])
            phase = "saved_key_handshake"
            session = Session(reader, writer, pairing.secret, receiving, audit.observe)
            task = asyncio.create_task(session.run())
            try:
                await asyncio.wait_for(session.wait_ready(), 12)
                emit("authenticated", endpoint="Windows", peer_proof="ULAB saved key")
                phase = "send"
                for mode in (list(Mode) if args.mode=="all" else [Mode[args.mode]]):
                    for kind in (list(generated) if args.kind=="all" else [args.kind]):
                        if args.receive_only:
                            break
                        if kind == "text":
                            job = store.stage_text(marker, mode)
                        else:
                            name = "ULAB-LAB.png" if kind == "image" else "ULAB-LAB.bin"
                            job = store.stage_file(temp / name, mode, kind=kind)
                        start = time.perf_counter_ns()
                        emit("send_started", mode=mode.name, kind=kind, bytes=job.size)
                        cpu = time.process_time_ns()
                        item_id = await session.send(job)
                        elapsed = time.perf_counter_ns() - start
                        processing = time.process_time_ns() - cpu
                        emit("complete_ack_received", mode=mode.name, kind=kind, bytes=job.size,
                             sha256_source=digest(generated[kind]), item_id=item_id.hex(),
                             completion_ms=round(elapsed/1e6,3),
                             windows_process_cpu_ms=round(processing/1e6,3))
                phase = "return"
                if args.listen_seconds:
                    emit("waiting_for_synthetic_return", seconds=args.listen_seconds)
                    wait = asyncio.create_task(audit.received.wait())
                    try:
                        if args.finish_on_return:
                            await asyncio.wait([task, wait], timeout=args.listen_seconds,
                                               return_when=asyncio.FIRST_COMPLETED)
                        else:
                            await asyncio.wait([task], timeout=args.listen_seconds)
                    finally:
                        wait.cancel()
                        await asyncio.gather(wait, return_exceptions=True)
                    if task.done():
                        await task
                    emit("return_window_closed", matched=audit.received.is_set())
                phase = "drain"
                await asyncio.wait_for(session.request_drain(), 8)
                result = await asyncio.wait_for(asyncio.shield(task), 12)
                clean = result == "drained"
                emit("session_ended", result=result)
            finally:
                await session.close()
                await asyncio.gather(task, return_exceptions=True)
    except Exception as exc:
        error = getattr(exc, "code", None)
        emit("blocked", phase=phase, reason=type(exc).__name__,
             causes=exception_types(exc),
             code=error if isinstance(error,str) and re.fullmatch("[a-z_]{1,60}",error) else None)
        return 2
    finally:
        if capture and capture.sessions:
            captured = await asyncio.to_thread(capture.finish, args.output, emit,
                                               clean and audit.capture_safe())
    return 0 if captured else 3


def exception_types(error):
    names = []
    while error is not None and len(names) < 6:
        names.append(type(error).__name__)
        error = error.__cause__ or error.__context__
    return names


def main():
    from usblinklab.protocol import Mode
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi", required=True, help="Pi USB gadget IPv4")
    parser.add_argument("--port", type=int, default=43123)
    parser.add_argument("--reuse-direct", action="store_true",
                        help="explicitly reuse the saved direct-USB peer without changing it")
    parser.add_argument("--tag", required=True, type=lambda v: v if re.fullmatch(r"[A-Z0-9-]{3,48}", v)
                        else parser.error("use a short synthetic tag of uppercase letters, digits or dashes"))
    parser.add_argument("--output", type=Path, required=True, help="new private result directory")
    parser.add_argument("--capture-dashboard-port", type=int, choices=range(1,65536), metavar="PORT",
                        help="optional local SSH forward to Pi dashboard port 8765; save two real PCAPNGs")
    parser.add_argument("--mode", required=True, choices=["all", *(m.name for m in Mode)])
    parser.add_argument("--kind", required=True, choices=["all","text","image","file"])
    parser.add_argument("--receive-only", action="store_true")
    parser.add_argument("--listen-seconds", type=int, choices=range(0,901), metavar="0..900", default=0)
    parser.add_argument("--finish-on-return", action="store_true")
    parser.add_argument("--private-receipt", action="store_true",
                        help="record mode/size/SHA only for other intentionally sent files; keep traces on the Pi")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
