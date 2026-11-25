#!/usr/bin/env python3
"""Local touchscreen dashboard. Capture privileges live in a separate Unix-socket service."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "capture" if (HERE/"capture").is_dir() else HERE.parents[1]/"capture"))
import ulab_capture as cap

TOKEN = secrets.token_urlsafe(32)
GUI_LOCK = threading.Lock()
gui_process = None
cache = {}
started = time.monotonic()


def command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cached(key, ttl, fn):
    now = time.monotonic()
    if key not in cache or now-cache[key][0] > ttl:
        cache[key] = now, fn()
    return cache[key][1]


def memory():
    data = {k: int(v.split()[0]) for k,v in (line.split(":",1) for line in Path("/proc/meminfo").read_text().splitlines())}
    return round(100*(1-data["MemAvailable"]/data["MemTotal"]),1)


def optional(fn):
    try:
        return fn()
    except (OSError, ValueError, KeyError, ZeroDivisionError, IndexError):
        return None


def phone():
    return any((d.parent / "product").is_file() and "iphone" in (d.parent / "product").read_text().lower()
               for d in Path("/sys/bus/usb/devices").glob("*/idVendor") if d.read_text().strip()=="05ac")


def graphical():
    socket_name = os.environ.get("WAYLAND_DISPLAY", "")
    return bool(socket_name and (Path(os.environ.get("XDG_RUNTIME_DIR","/nonexistent"))/socket_name).is_socket()
                and shutil.which("wireshark"))


def status():
    data = optional(lambda:json.loads(Path("/run/ulab-relay/status.json").read_text())) or {}
    gadget = optional(lambda:any("DEVTYPE=gadget" in p.read_text().splitlines() and
                                 (p.parent/"carrier").read_text().strip()=="1"
                                 for p in Path("/sys/class/net").glob("*/uevent")))
    mux = cached("mux",4,lambda: command(["idevice_id","-l"]))
    throttled = cached("power",5,lambda: command(["vcgencmd","get_throttled"]))
    proc = cached("service",2,lambda: command(["systemctl","show","ulab-relay","-p","ActiveState","--value"]))
    capture = optional(lambda:cap.request_ctl({"action":"list"}))
    return {"relay":data,"health":{"temperature": optional(lambda:round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text())/1000,1)),
                  "memory_percent":optional(memory),"throttled":throttled,"iphone_usb":optional(phone),
                  "gadget_up":gadget,"service":proc,"iphone_mux":bool(mux) if mux is not None else None},
            "capture":capture or {"interfaces":{},"captures":[],"active":[],"free":None,"unavailable":True},
            "local_display":graphical(),"token":TOKEN,"uptime":round(time.monotonic()-started)}


def open_gui(action, req):
    global gui_process
    if not graphical():
        raise ValueError("Pi desktop session is unavailable")
    lua = "/opt/ulab/dissectors/ulab.lua"
    base = ["wireshark","-X","lua_script:"+lua]
    with GUI_LOCK:
        if gui_process is not None and gui_process.poll() is None:
            raise ValueError("Wireshark is already open on the Pi display")
        if action=="open":
            sid = cap.validate_id(req["id"])
            item = next((r for r in cap.request_ctl({"action":"list"})["captures"] if r["id"]==sid),None)
            if not item or item["result"]!="complete":
                raise ValueError("capture is not complete")
            gui_process = subprocess.Popen(base+["-r",str(cap.STATE/(sid+".pcapng"))],start_new_session=True)
        else:
            # The live file is also retained by the bounded capture service.
            layer = req.get("layer","gadget")
            if layer not in ("gadget","loopback"):
                raise ValueError("live USB diagnostics use explicit private capture and offline open")
            secs, mib = cap.bounded(req.get("seconds",30),120), cap.bounded(req.get("mib",8),32)
            cli = [sys.executable,cap.__file__,"stream","--layer",layer,"--seconds",str(secs),"--mib",str(mib)]
            producer = subprocess.Popen(cli,stdout=subprocess.PIPE)
            try:
                gui_process = subprocess.Popen(base+["-k","-i","-"],stdin=producer.stdout,start_new_session=True)
            finally:
                producer.stdout.close()
                threading.Thread(target=producer.wait,daemon=True).start()


class Page(BaseHTTPRequestHandler):
    server_version = "USBLinkLab"
    def log_message(self, *_):
        return

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def host_ok(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}")

    def headers_for(self, length, mime, code=200):
        self.send_response(code)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(length))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()

    def json(self,obj,code=200):
        raw = json.dumps(obj,ensure_ascii=False).encode()
        self.headers_for(len(raw),"application/json; charset=utf-8",code)
        self.wfile.write(raw)

    def do_GET(self):
        if not self.host_ok():
            return self.send_error(403)
        path=urlsplit(self.path).path
        if path=="/api/status":
            return self.json(status())
        if path=="/":
            html = (HERE/"dashboard.html").read_bytes()
            self.headers_for(len(html),"text/html; charset=utf-8")
            self.wfile.write(html)
        elif path.startswith("/download/"):
            sid = path[len("/download/"):]
            if not cap.ID.fullmatch(sid):
                return self.send_error(404)
            f = cap.STATE/(sid+".pcapng")
            if f.is_symlink() or not f.is_file():
                return self.send_error(404)
            try:
                meta=json.loads(f.with_suffix(".json").read_text())
                if meta.get("result")!="complete":
                    return self.send_error(409)
                with f.open("rb") as src:
                    self.headers_for(os.fstat(src.fileno()).st_size,"application/octet-stream")
                    shutil.copyfileobj(src,self.wfile)
            except FileNotFoundError:
                return self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.host_ok() or not secrets.compare_digest(self.headers.get("X-ULAB-Token",""),TOKEN):
            return self.send_error(403)
        if self.headers.get("Origin") not in (None,"http://"+self.headers["Host"]):
            return self.send_error(403)
        try:
            n=int(self.headers.get("Content-Length","0"))
            if not 1<=n<=2048 or self.headers.get_content_type()!="application/json":
                return self.send_error(400)
            req=json.loads(self.rfile.read(n))
            if not isinstance(req,dict):
                raise ValueError("invalid request")
            action=self.path.removeprefix("/api/")
            if action in ("start","stop"):
                val=cap.request_ctl({**req,"action":action})
            elif action in ("live","open"):
                open_gui(action,req)
                val=None
            else:
                return self.send_error(404)
            return self.json({"ok":True,"value":val})
        except (ValueError,KeyError,TypeError,OSError) as exc:
            return self.json({"ok":False,"error":str(exc)[:160]},400)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1",args.port),Page).serve_forever()
