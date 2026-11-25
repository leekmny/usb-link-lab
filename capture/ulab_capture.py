#!/usr/bin/env python3
"""Bounded PCAPNG capture on explicitly identified USBLinkLab links."""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid

CONF = Path("/etc/ulab/relay.json")
STATE = Path("/var/lib/ulab-capture")
SOCKET = "/run/ulab-capture/control.sock"
MIB = 1024 * 1024
MAX_TOTAL = 256 * MIB
MAX_FILES = 16
ID = re.compile(r"[0-9a-f]{32}")
LOCK = threading.RLock()
ACTIVE = {}
STARTED = time.monotonic()


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bounded(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("duration or size outside permitted range")
    return value


def files(path=STATE):
    return sorted((p for p in path.glob("*.pcapng")
                   if ID.fullmatch(p.stem) and not p.is_symlink() and p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def scope(candidates, layer, extra, private):
    if layer not in candidates:
        raise ValueError("capture interface unavailable")
    if layer == "usbmon" and private is not True:
        raise ValueError("USB diagnostics require explicit private capture selection")
    if not isinstance(extra, str) or len(extra) > 160 or not re.fullmatch(r"[a-zA-Z0-9 :.\/\[\]&=><!_-]*", extra):
        raise ValueError("unsupported capture filter; parentheses and escaping are reserved")
    target = candidates[layer]
    base = target["filter"]
    return target["interface"], f"({base}) and ({extra})" if extra else base


def discover():
    options = {}
    try:
        cfg = json.loads(CONF.read_text())
        iface = cfg["interface"]
        if re.fullmatch(r"[\w.-]{1,15}", iface) and "DEVTYPE=gadget" in (Path("/sys/class/net") / iface / "uevent").read_text().splitlines():
            peer = str(ipaddress.IPv4Address(cfg["windows_address"]))
            addr = str(ipaddress.IPv4Address(cfg["listen_address"]))
            port = bounded(cfg.get("listen_port", 43123), 65535)
            options["gadget"] = {"interface": iface, "filter": f"host {addr} and host {peer} and tcp port {port}"}
        port = bounded(cfg.get("upstream_port", 43124), 65535)
        options["loopback"] = {"interface": "lo", "filter": f"host 127.0.0.1 and tcp port {port}"}
    except (ValueError, KeyError, OSError):
        pass
    # The private pinned UDID itself never enters status, commands or manifest.
    try:
        # Installer gives this service only the USB sysfs identity of the selected phone via a group-readable file.
        pinned = Path("/etc/ulab/capture-device").read_text().strip().replace("-", "").lower()
        if not re.fullmatch("[a-f0-9]{16,64}", pinned):
            return options
        for d in Path("/sys/bus/usb/devices").glob("*"):
            try:
                if (d / "idVendor").read_text().strip() != "05ac" or (d / "serial").read_text().strip().lower().replace("-", "") != pinned:
                    continue
                bus, dev = (int((d / k).read_text()) for k in ("busnum", "devnum"))
                if Path(f"/dev/usbmon{bus}").exists() and 1 <= dev <= 127:
                    options["usbmon"] = {"interface": f"usbmon{bus}", "filter": f"link[11] = {dev}"}
            except (ValueError, OSError):
                continue
    except OSError:
        pass
    return options


def capture_args(request, path):
    layer = request["layer"]
    iface, expr = scope(discover(), layer, request.get("filter", ""), request.get("private", False))
    seconds = bounded(request["seconds"], 30 if layer == "usbmon" else 120)
    mib = bounded(request["mib"], 32)
    exe = shutil.which("dumpcap")
    if not exe:
        raise ValueError("dumpcap unavailable")
    return [exe, "-i", iface, "-p", "-g", "-f", expr, "-s", "262144", "-a", f"duration:{seconds}",
            "-a", f"filesize:{mib * 1024}", "-q", "-w", str(path)], iface, expr


def write_meta(sid, data):
    p = STATE / (sid + ".json")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(p)


def reclaim():
    entries = files()
    total = sum(p.stat().st_size for p in entries)
    for p in reversed(entries):
        if total <= MAX_TOTAL - 64*MIB and len(entries) < MAX_FILES:
            break
        if p.stem in ACTIVE:
            continue
        total -= p.stat().st_size
        p.unlink()
        p.with_suffix(".json").unlink(missing_ok=True)
        entries = [e for e in entries if e != p]
    if shutil.disk_usage(STATE).free < 80*MIB:
        raise ValueError("insufficient free space")


def inventory():
    recent = []
    with LOCK:
        for p in files():
            try:
                meta = json.loads(p.with_suffix(".json").read_text())
                meta["size"] = p.stat().st_size
                recent.append(meta)
            except (ValueError, OSError):
                continue
        return {"interfaces": discover(), "captures": recent, "active": list(ACTIVE),
                "free": shutil.disk_usage(STATE).free, "uptime": round(time.monotonic()-STARTED)}


def observe(p, sid, record, path):
    _, log = p.communicate()
    try:
        record["ended_utc"] = utc()
        record["result"] = "complete" if p.returncode == 0 else "capture_error"
        record["exit_code"] = p.returncode
        record["size"] = path.stat().st_size if path.exists() else 0
        if path.is_file() and record["size"]:
            with path.open("rb") as f:
                record["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
            result = subprocess.run(["capinfos", "-T", "-r", "-c", str(path)], capture_output=True, timeout=15)
            last = result.stdout.decode(errors="replace").strip().split("\t")[-1]
            record["packets"] = int(last) if last.isdigit() else None
        # Some libpcap sources omit drop counters. Absence stays null.
        drops = re.search(rb"received/dropped on interface.*?:\s*\d+/(\d+)", log or b"", re.I)
        record["dropped"] = int(drops[1]) if drops else None
    except (ValueError, OSError, subprocess.SubprocessError):
        record["diagnostics"] = "summary_unavailable"
    finally:
        with LOCK:
            write_meta(sid, record)
            ACTIVE.pop(sid, None)


def begin(request):
    sid = uuid.uuid4().hex
    path = STATE / (sid + ".pcapng")
    args, iface, expr = capture_args(request, path)
    with LOCK:
        if len(ACTIVE) >= 2:
            raise ValueError("two captures already active")
        reclaim()
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for _ in range(40):
                if p.poll() is not None:
                    if not path.exists() or not path.stat().st_size:
                        raise ValueError("capture did not start; check filter and capture permissions")
                    break
                if path.exists() and path.stat().st_size >= 12:
                    break
                time.sleep(.025)
            record = {"id": sid, "started_utc": utc(), "layer": request["layer"], "interface": iface,
                      "filter": expr, "seconds": request["seconds"], "mib": request["mib"],
                      "result": "capturing", "packets": None, "dropped": None, "format": "pcapng",
                      "tool": subprocess.check_output([args[0], "--version"], text=True).splitlines()[0],
                      "revision": Path("/opt/ulab/capture/REVISION").read_text().strip() if Path("/opt/ulab/capture/REVISION").is_file() else "local"}
            write_meta(sid, record)
        except Exception:
            if p.poll() is None:
                p.terminate()
            p.communicate()
            path.unlink(missing_ok=True)
            raise
        ACTIVE[sid] = p
        threading.Thread(target=observe, args=(p, sid, record, path), daemon=True).start()
    return sid


def validate_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid session ID")
    return value


def request_ctl(data):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(8)
        s.connect(SOCKET)
        s.sendall(json.dumps(data).encode()+b"\n")
        with s.makefile("rb") as f:
            result = json.loads(f.readline(128*1024))
    if not result["ok"]:
        raise ValueError(result["error"])
    return result["value"]


def stop(sid):
    with LOCK:
        p = ACTIVE.get(validate_id(sid))
        if p is None:
            raise ValueError("session is not active")
        p.send_signal(signal.SIGINT)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            line = self.rfile.readline(1025)
            if len(line) > 1024:
                raise ValueError("request too large")
            req = json.loads(line)
            action = req["action"]
            if action == "list":
                result = inventory()
            elif action == "start":
                result = begin(req)
            elif action == "stop":
                result = stop(req["id"])
            else:
                raise ValueError("unsupported action")
            reply = {"ok": True, "value": result}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
            reply = {"ok": False, "error": str(e)[:180]}
        self.wfile.write(json.dumps(reply).encode()+b"\n")


def serve():
    os.umask(0o027)
    Path(SOCKET).unlink(missing_ok=True)
    with socketserver.UnixStreamServer(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


def stream(req):
    # Read the same bounded PCAPNG as the dashboard. No binary data passes through a text pipeline.
    sid = request_ctl(req)
    p = STATE / (sid + ".pcapng")
    deadline = time.monotonic() + req["seconds"] + 12
    try:
        with p.open("rb") as f:
            while time.monotonic() < deadline:
                chunk = f.read(65536)
                if chunk:
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                elif sid not in request_ctl({"action": "list"})["active"]:
                    sys.stdout.buffer.write(f.read())
                    sys.stdout.buffer.flush()
                    return
                else:
                    time.sleep(.15)
    finally:
        try:
            request_ctl({"action": "stop", "id": sid})
        except (OSError, ValueError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["serve", "list", "start", "stop", "stream"])
    parser.add_argument("--layer", choices=["gadget","loopback","usbmon"], default="gadget")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--mib", type=int, default=8)
    parser.add_argument("--filter", default="")
    parser.add_argument("--private-usb", action="store_true")
    parser.add_argument("--id", default="")
    a = parser.parse_args()
    req = {"action": "start" if a.action == "stream" else a.action, "layer": a.layer,
           "seconds": a.seconds, "mib": a.mib, "filter": a.filter, "private": a.private_usb, "id": a.id}
    if a.action == "serve":
        serve()
    elif a.action == "stream":
        stream(req)
    else:
        print(json.dumps(request_ctl(req), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
