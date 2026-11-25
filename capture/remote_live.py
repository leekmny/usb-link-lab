#!/usr/bin/env python3
"""Stream a bounded laboratory capture via pinned OpenSSH into Wireshark or a PCAPNG."""
import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True, help="management DNS/IP, pre-enrolled in known hosts")
    p.add_argument("--user",required=True)
    p.add_argument("--key", required=True, type=Path)
    p.add_argument("--known-hosts",required=True,type=Path)
    p.add_argument("--layer",choices=["gadget","loopback","usbmon"],default="gadget")
    p.add_argument("--seconds",type=int,choices=range(1,121),metavar="1..120",default=30)
    p.add_argument("--mib",type=int,choices=range(1,33),metavar="1..32",default=8)
    p.add_argument("--filter",default="")
    p.add_argument("--private-usb",action="store_true")
    p.add_argument("--save",type=Path)
    p.add_argument("--no-gui",action="store_true")
    a=p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*",a.host) or not re.fullmatch(r"[a-z_][a-z0-9_-]*",a.user):
        p.error("invalid SSH host or user")
    if a.no_gui and a.save is None:
        p.error("--no-gui requires --save")
    remote=["python3","/opt/ulab/capture/ulab_capture.py","stream","--layer",a.layer,"--seconds",str(a.seconds),
            "--mib",str(a.mib),"--filter",a.filter]
    if a.private_usb:
        remote.append("--private-usb")
    args=["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=8","-o","StrictHostKeyChecking=yes",
          "-o","IdentitiesOnly=yes","-o","UserKnownHostsFile="+str(a.known_hosts.resolve(strict=True)),
          "-i",str(a.key.resolve(strict=True)),a.user+"@"+a.host,shlex.join(remote)]
    viewer=None
    target=None
    ssh=None
    size=0
    try:
        if a.save:
            target=a.save.open("xb")
        if not a.no_gui:
            exe=shutil.which("wireshark") or str(Path(os.environ.get("ProgramFiles",""))/"Wireshark"/"Wireshark.exe")
            lua=Path(__file__).resolve().parents[1]/"dissectors"/"ulab.lua"
            viewer=subprocess.Popen([exe,"-X","lua_script:"+str(lua),"-k","-i","-"],stdin=subprocess.PIPE,bufsize=0)
        ssh=subprocess.Popen(args,stdout=subprocess.PIPE,bufsize=0)
        header=ssh.stdout.read(4)
        if header!=b"\x0a\x0d\x0d\x0a":
            raise RuntimeError("remote capture did not return PCAPNG")
        chunk=header
        while chunk:
            if target:
                target.write(chunk)
            if viewer:
                viewer.stdin.write(chunk)
            size+=len(chunk)
            chunk=ssh.stdout.read(65536)
        if ssh.wait()!=0:
            raise RuntimeError("remote capture was interrupted")
        print(f"Received {size} PCAPNG bytes.",file=sys.stderr)
    finally:
        if ssh is not None and ssh.poll() is None:
            ssh.terminate()
            ssh.wait()
        if target:
            target.close()
        if viewer and viewer.stdin:
            viewer.stdin.close()


if __name__=="__main__":
    main()
