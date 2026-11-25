#!/usr/bin/env python3
"""Run the system iproxy with explicit USB-only device and loopback binding."""
import os
from pathlib import Path
import re
import subprocess
import sys

from relay import Settings, event


UDID = re.compile(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{8}-[a-fA-F0-9]{16})\Z")


def iproxy_argv(help_text, udid, local_port):
    for token in ("--local", "--udid", "--source", "LOCAL_PORT:DEVICE_PORT"):
        if token not in help_text:
            raise ValueError("installed iproxy lacks required USB-only syntax")
    if not UDID.fullmatch(udid):
        raise ValueError("invalid device identifier")
    return ["/usr/bin/iproxy", "-l", "-u", udid, "-s", "127.0.0.1", f"{local_port}:43123"]


def main():
    try:
        cfg = Settings.load("/etc/ulab/relay.json")
        udid = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "iphone.udid").read_text().strip()
        child_env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
        help_text = subprocess.check_output(["/usr/bin/iproxy", "--help"], text=True,
                                            stderr=subprocess.STDOUT, env=child_env, timeout=5)
        argv = iproxy_argv(help_text, udid, cfg.upstream_port)
        event("iproxy_starting", local_port=cfg.upstream_port, device_port=43123)
        # iproxy may print device identifiers. Keep its streams out of the journal.
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        os.execve(argv[0], argv, child_env)
    except (OSError, KeyError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        event("iproxy_unavailable", reason=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
