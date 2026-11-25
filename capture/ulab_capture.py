#!/usr/bin/env python3
"""Bounded PCAPNG capture on explicitly identified USBLinkLab links."""
import argparse
from collections import OrderedDict
from decimal import Decimal
import queue
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
STOP_REQUESTED = set()
OBSERVE_QUEUE = queue.Queue(maxsize=MAX_FILES)
OBSERVER_MAX_PACKETS = 20_000
OBSERVER_MAX_OUTPUT = 4 * MIB
DISSECTOR = Path(__file__).resolve().parents[1] / "dissectors" / "ulab.lua"


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


def mux_bulk_endpoints(device):
    """One active ff/fe/02 interface with exactly one bulk endpoint per direction."""
    try:
        if (device/"authorized").read_text().strip() != "1":
            return None
        config = (device/"bConfigurationValue").read_text().strip()
        if not config.isdecimal() or not 1 <= int(config) <= 255:
            return None
        matches = []
        for interface in device.glob(f"{device.name}:{config}.*"):
            identity = tuple(int((interface/k).read_text().strip(), 16)
                             for k in ("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"))
            if identity == (0xff, 0xfe, 0x02):
                matches.append(interface)
        if len(matches) != 1:
            return None
        interface, = matches
        if int((interface/"bNumEndpoints").read_text().strip(), 16) != 2:
            return None
        addresses = []
        for ep in interface.glob("ep_*"):
            address = int((ep/"bEndpointAddress").read_text().strip(), 16)
            if (ep.name.lower() != f"ep_{address:02x}" or (address & 0x0f) == 0
                    or address & ~0x8f or int((ep/"bmAttributes").read_text().strip(), 16) != 2):
                return None
            addresses.append(address)
        addresses.sort()
        if len(addresses) == 2 and addresses[0] < 0x80 <= addresses[1]:
            return tuple(addresses)
    except (ValueError, OSError):
        pass
    return None


def usbmon_filter(device_address, endpoints):
    # Binary usbmon URB header offsets, shared by Linux USB and USB mmapped.
    out_ep, in_ep = endpoints
    return (f"link[11] = {device_address} and link[9] = 3 and "
            f"(link[10] = {out_ep} or link[10] = {in_ep})")


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
                endpoints = mux_bulk_endpoints(d)
                if endpoints and 1 <= bus <= 65535 and 1 <= dev <= 127 and Path(f"/dev/usbmon{bus}").exists():
                    options["usbmon"] = {"interface": f"usbmon{bus}", "filter": usbmon_filter(dev, endpoints)}
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


def frame_summary(path, port):
    """Read structural ULAB fields only. Counters are per reassembled frame."""
    if path.is_symlink() or path.stat().st_size > 34*MIB:
        return {"status": "limited"}
    fields = ("frame.number", "frame.time_epoch", "tcp.srcport", "ulab.mode",
              "ulab.frame_type", "ulab.body_len", "ulab.item_id", "ulab.invalid")
    cmd = ["tshark", "-n", "-X", "lua_script:"+str(DISSECTOR),
           "-o", "tcp.desegment_tcp_streams:TRUE", "-o", f"ulab.port:{port}",
           "-o", "ulab.iproxy_port:0", "-c", str(OBSERVER_MAX_PACKETS+1),
           "-r", str(path), "-T", "fields", "-E", "occurrence=a"]
    for name in fields:
        cmd.extend(("-e", name))
    counts = [{"c2s":0, "s2c":0} for _ in range(4)]
    recent = OrderedDict()
    status, used = "observed", 0
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        expired = threading.Event()
        def timeout():
            expired.set()
            proc.kill()
        timer = threading.Timer(18, timeout)
        timer.daemon = True
        timer.start()
        try:
            for packet_index in range(OBSERVER_MAX_PACKETS+1):
                line = proc.stdout.readline(128*1024)
                if not line:
                    break
                used += len(line)
                if used > OBSERVER_MAX_OUTPUT or not line.endswith(b"\n") or packet_index == OBSERVER_MAX_PACKETS:
                    status = "limited"
                    proc.kill()
                    break
                parts = line.rstrip(b"\r\n").decode("ascii").split("\t")
                if len(parts) != len(fields):
                    raise ValueError("unexpected observer output")
                _, epoch, source, modes, types, sizes, ids, invalid = parts
                if invalid:
                    status = "partial"
                if not modes:
                    continue
                direction = "s2c" if int(source) == port else "c2s"
                for mode, typ, size, opaque in zip(modes.split(","), types.split(","),
                                                     sizes.split(","), ids.split(","), strict=True):
                    mode, typ, size = int(mode), int(typ), int(size)
                    if not (0 <= mode <= 3 and 1 <= typ <= 7 and 0 <= size <= 65536):
                        raise ValueError("invalid observed field")
                    counts[mode][direction] += 1
                    if typ not in (1, 3, 4):
                        continue
                    key = (direction, opaque)
                    if typ == 1:
                        if len(recent) == 6:
                            recent.popitem(last=False)
                        local_id = hashlib.sha256((path.stem+direction+opaque).encode()).hexdigest()[:8]
                        recent[key] = dict(local_id=local_id, mode=mode, direction=direction,
                                           unix_ms=int(Decimal(epoch)*1000), data_bytes=0, end_seen=False)
                    elif key in recent:
                        if typ == 3:
                            recent[key]["data_bytes"] += size
                        else:
                            recent[key]["end_seen"] = True
            code = proc.wait()
            if expired.is_set():
                status = "limited"
            elif code and status != "limited":
                return {"status":"unavailable"}
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
    if status == "observed" and not any(any(c.values()) for c in counts):
        status = "no_frames"
    return {"status":status, "authentication":"unverified", "frames":counts,
            "items":list(reversed(recent.values()))}


def queue_observation(meta):
    if meta.get("layer") not in ("gadget", "loopback"):
        return {"status":"unsupported"}
    # Only the service-generated base filter sets this port. Ignore any narrower suffix.
    match = re.search(r"\btcp port ([0-9]{1,5})\b", meta.get("filter", ""))
    if not match or not 1 <= int(match[1]) <= 65535:
        return {"status":"unavailable"}
    try:
        OBSERVE_QUEUE.put_nowait((meta["id"], int(match[1])))
    except queue.Full:
        return {"status":"limited"}
    return {"status":"pending"}


def observe_worker():
    while True:
        sid, port = OBSERVE_QUEUE.get()
        try:
            path = STATE / (sid+".pcapng")
            try:
                result = frame_summary(path, port)
            except (OSError, ValueError, ArithmeticError, subprocess.SubprocessError):
                result = {"status":"unavailable"}
            with LOCK:
                meta_path = path.with_suffix(".json")
                if path.is_file() and not meta_path.is_symlink() and meta_path.is_file():
                    meta = json.loads(meta_path.read_text())
                    meta["observation"] = result
                    write_meta(sid, meta)
        except (OSError, ValueError):
            pass
        finally:
            OBSERVE_QUEUE.task_done()


def recover_observations():
    """Mark abandoned live files and schedule bounded summaries for older captures."""
    with LOCK:
        for path in files(STATE)[:MAX_FILES]:
            try:
                meta_path = path.with_suffix(".json")
                if meta_path.is_symlink():
                    continue
                meta = json.loads(meta_path.read_text())
                if meta.get("id") != path.stem:
                    continue
                if meta.get("result") == "capturing":
                    meta["result"] = "interrupted"
                    meta["interrupted_detected_utc"] = utc()
                    meta["observation"] = {"status":"interrupted"}
                elif meta.get("result") == "complete" and meta.get("observation", {}).get("status") in (None, "pending"):
                    meta["observation"] = queue_observation(meta)
                else:
                    continue
                write_meta(path.stem, meta)
            except (OSError, ValueError, TypeError, AttributeError):
                continue


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
            if sid in STOP_REQUESTED:
                record["completion_reason"] = "user_stopped"
                STOP_REQUESTED.discard(sid)
            if record["result"] == "complete":
                record["observation"] = queue_observation(record)
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
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
            STOP_REQUESTED.add(sid)


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
    recover_observations()
    threading.Thread(target=observe_worker, name="ulab-observer", daemon=True).start()
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
