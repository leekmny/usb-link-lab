"""Kernel facts used to admit only the configured USB gadget data path."""
from ipaddress import IPv4Interface, IPv4Address
import json
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys


SYS_NET = Path("/sys/class/net")
IFACE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}\Z")


def gadget_up(path):
    try:
        return ("DEVTYPE=gadget" in (path / "uevent").read_text().splitlines()
                and (path / "carrier").read_text().strip() == "1")
    except OSError:
        return False


def addresses(interface):
    if not IFACE_NAME.fullmatch(interface):
        raise ValueError("invalid interface name")
    result = subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", interface],
                            capture_output=True, check=True, timeout=3)
    return [IPv4Interface(f"{a['local']}/{a['prefixlen']}")
            for entry in json.loads(result.stdout) for a in entry.get("addr_info", [])
            if a["family"] == "inet"]


def validate_path(config):
    if not gadget_up(SYS_NET / config.interface):
        raise ValueError("selected interface is not an active kernel USB Ethernet gadget")
    host = IPv4Address(config.listen_address)
    peer = IPv4Address(config.windows_address)
    matches = [addr for addr in addresses(config.interface) if addr.ip == host]
    if not any(peer in addr.network and
               (addr.network.prefixlen >= 31 or peer not in (addr.network.broadcast_address,
                                                             addr.network.network_address))
               for addr in matches):
        raise ValueError("selected addresses are not a direct gadget subnet pair")


def listener(config):
    validate_path(config)
    with_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Linux restricts both receive and reply traffic to this interface.
        with_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                               config.interface.encode("ascii") + b"\x00")
        with_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with_socket.bind((config.listen_address, config.listen_port))
        with_socket.listen(4)
        with_socket.setblocking(False)
        return with_socket
    except BaseException:
        with_socket.close()
        raise


def owned_loopback_listener(rows, port, uid):
    # /proc/net/tcp stores IPv4 in host byte order. Its state 0A means LISTEN.
    host = int.from_bytes(socket.inet_aton("127.0.0.1"), sys.byteorder)
    address = f"{host:08X}:{port:04X}"
    for row in rows:
        fields = row.split()
        if len(fields) > 7 and fields[1] == address and fields[3] == "0A":
            return fields[7] == str(uid)
    return False


def directory_uid(info):
    if stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022 and 0 < info.st_uid < 4294967295:
        return info.st_uid
    return None


def iproxy_owns_port(port):
    try:
        # PID 1 creates this dedicated RuntimeDirectory inside root-owned /run.
        # Sandboxed relay processes need neither NSS integration nor system bus access.
        uid = directory_uid(Path("/run/ulab-iproxy").lstat())
        if uid is None:
            return False
        with Path("/proc/net/tcp").open() as table:
            return owned_loopback_listener(table, port, uid)
    except OSError:
        return False
