#!/usr/bin/env python3
"""Select one verified gadget and one explicitly trusted wired iPhone on the Pi."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import gadget
from relay import Settings
from usb_proxy import UDID, iproxy_argv


def select_device(devices, only):
    if not devices or not all(UDID.fullmatch(item) for item in devices):
        raise ValueError("USB-only discovery found no suitable phone")
    if len(devices) == 1:
        return devices[0]
    if only:
        raise ValueError("multiple USB devices are attached; run interactively to select")
    for i, udid in enumerate(devices, 1):
        print(f"{i}. USB device ending in {udid[-4:]}")
    index = int(input("Choose the lab iPhone number: "))
    if not 1 <= index <= len(devices):
        raise ValueError("invalid USB device choice")
    return devices[index - 1]


def atomic_file(path, contents, mode):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as tmp:
        name = Path(tmp.name)
        try:
            tmp.write(contents)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.chmod(name, mode)
        except BaseException:
            name.unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def write_config(root, config, udid):
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    atomic_file(root / "iphone.udid", udid + "\n", 0o600)
    atomic_file(root / "relay.json", json.dumps(asdict(config), indent=2) + "\n", 0o644)


def configure(args):
    if os.geteuid() != 0:
        raise ValueError("run configure.py with sudo on the Raspberry Pi")
    candidates = [path.name for path in gadget.SYS_NET.iterdir() if gadget.gadget_up(path)]
    if not candidates:
        raise ValueError("no active USB gadget; verify the cable, official gadget and Windows driver")
    interface = args.interface
    if not interface:
        print("Active USB gadgets: " + ", ".join(sorted(candidates)))
        interface = candidates[0] if len(candidates) == 1 else input("Gadget interface: ").strip()
    if interface not in candidates:
        raise ValueError("chosen interface is not an active USB gadget")
    available = [str(addr.ip) for addr in gadget.addresses(interface)]
    if not available:
        raise ValueError("gadget has no IPv4 address")
    local = args.listen_address
    if not local:
        print("Gadget addresses: " + ", ".join(available))
        local = available[0] if len(available) == 1 else input("Choose the gadget IPv4: ").strip()
    windows = args.windows_address or input("Windows gadget adapter IPv4 address: ").strip()
    config = Settings.parse(dict(interface=interface, listen_address=local, windows_address=windows,
                                 listen_port=args.listen_port, upstream_port=args.upstream_port))
    gadget.validate_path(config)

    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
    result = subprocess.run(["idevice_id", "-l"], capture_output=True, text=True, env=env,
                            check=True, timeout=8)
    device = select_device(result.stdout.splitlines(), args.only_usb_device)
    usage = subprocess.check_output(["iproxy", "--help"], stderr=subprocess.STDOUT,
                                    text=True, env=env, timeout=5)
    iproxy_argv(usage, device, config.upstream_port)
    for action in (["pair", "validate"] if args.pair else ["validate"]):
        check = subprocess.run(["idevicepair", "-u", device, action], capture_output=True,
                               env=env, timeout=30)
        if check.returncode:
            raise ValueError("Apple host trust pending. Unlock the lab iPhone, run with --pair, "
                             "tap Trust, and retry. The ULAB app invitation is separate.")

    units = ["ulab-relay.service", "ulab-iproxy.service"]
    active = subprocess.run(["systemctl", "is-active", "--quiet", *units]).returncode == 0
    if active and not args.enable:
        raise ValueError("use --enable when replacing an active configuration")
    if active:
        subprocess.run(["systemctl", "stop", *units], check=True)
    write_config(Path("/etc/ulab"), config, device)
    print(f"Configured {interface} {local}:{config.listen_port}; USB-only iPhone selected.")
    if args.enable:
        subprocess.run(["systemctl", "enable", "--now", "ulab-relay.service"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface")
    parser.add_argument("--listen-address")
    parser.add_argument("--windows-address")
    parser.add_argument("--listen-port", type=int, default=43123)
    parser.add_argument("--upstream-port", type=int, default=43124)
    parser.add_argument("--only-usb-device", action="store_true",
                        help="require exactly one attached USB device")
    parser.add_argument("--pair", action="store_true", help="request normal Apple host trust")
    parser.add_argument("--enable", action="store_true", help="start services and enable at boot")
    try:
        configure(parser.parse_args())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # A subprocess error can include the full UDID in its command representation.
        print(str(exc) if isinstance(exc, ValueError) else type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
