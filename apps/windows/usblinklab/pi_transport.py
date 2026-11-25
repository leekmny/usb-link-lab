"""Explicit Windows USB-gadget path for the existing opaque ULAB stream."""
import asyncio
import base64
import ctypes
from dataclasses import asdict, dataclass
from ipaddress import IPv4Address, IPv4Network
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
from uuid import UUID

from . import transport
from .pairing import PairingError, PairingStore, RELAY_PEER, atomic_save
from .transport import Device, TransportError


@dataclass(frozen=True)
class PathSettings:
    mode: str = "direct"
    destination: str = ""
    port: int = 43123
    reuse_direct: bool = False

    def validate(self):
        if self.mode not in ("direct", "relay") or type(self.reuse_direct) is not bool:
            raise TransportError("pi_config_invalid")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise TransportError("pi_config_invalid")
        if self.mode == "relay":
            address(self.destination)

    @classmethod
    def load(cls, root):
        try:
            with (root / "transport.json").open(encoding="utf-8") as file:
                settings = cls(**json.load(file))
            settings.validate()
            return settings
        except FileNotFoundError:
            return cls()  # Original installations continue using direct USB.
        except (ValueError, OSError, TypeError, TransportError):
            # Remain disconnected until the operator explicitly saves a valid path.
            return cls("invalid")

    def save(self, root):
        self.validate()
        atomic_save(root / "transport.json", json.dumps(asdict(self)).encode("utf-8"))

    def provider(self, root):
        if self.mode == "direct":
            return transport
        return PiRelay(self, PairingStore(root / "pairing.dpapi"))


def address(text):
    try:
        if not isinstance(text, str):
            raise ValueError
        value = IPv4Address(text)
        if value.is_unspecified or value.is_loopback or value.is_multicast or value.is_reserved:
            raise ValueError
        return value
    except ValueError:
        raise TransportError("pi_config_invalid") from None


@dataclass(frozen=True)
class GadgetLink:
    guid: str
    index: int
    source: str
    alias: str


def verified_link(row, destination):
    """Accept only a preferred local address and a direct route on official gadget hardware."""
    target = address(destination)
    try:
        source = address(row["source"])
        net = IPv4Network((source, row["length"]), strict=False)
        route = IPv4Network(row["prefix"])
        if (row["up"] is not True or row["physical"] is not True or not re.fullmatch(
                r"USB\\VID_2E8A&PID_0013(?:&MI_[0-9A-F]{2})?\\[^\\]+",
                row["pnp"], re.IGNORECASE)):
            raise TransportError("pi_adapter_missing")
        index = row["index"]
        if (type(index) is not int or not 0 < index < 0x1000000
                or not index == row["route_index"] == row["source_index"]
                or row["preferred"] is not True or row["skip"] is not False
                or row["next_hop"] != "0.0.0.0" or net.prefixlen == 0
                or not route.subnet_of(net) or target not in route or target == source
                or (net.prefixlen < 31 and target in (net.network_address, net.broadcast_address))):
            raise TransportError("pi_route_invalid")
        return GadgetLink(str(UUID(row["guid"])), index, str(source), row["alias"])
    except (TypeError, KeyError, ValueError, AttributeError):
        raise TransportError("pi_route_invalid") from None


# Find-NetRoute without an interface constraint returns the route Windows actually prefers.
# Keep management NICs out of discovery. No TCP connection is opened here.
_ROUTE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$result = @(Find-NetRoute -RemoteIPAddress $env:ULAB_ROUTE_TARGET)
$src = @($result | Where-Object {$_.CimClass.CimClassName -eq 'MSFT_NetIPAddress'})
$route = @($result | Where-Object {$_.CimClass.CimClassName -eq 'MSFT_NetRoute'})
if ($src.Count -ne 1 -or $route.Count -ne 1) { exit 2 }
$nic = Get-NetAdapter -InterfaceIndex $route[0].InterfaceIndex
@{index=[int]$nic.ifIndex; route_index=[int]$route[0].InterfaceIndex
  source_index=[int]$src[0].InterfaceIndex; source=$src[0].IPAddress; length=[int]$src[0].PrefixLength
  prefix=$route[0].DestinationPrefix; next_hop=$route[0].NextHop
  preferred=($src[0].AddressState -eq 'Preferred'); skip=[bool]$src[0].SkipAsSource
  pnp=$nic.PnPDeviceID; up=($nic.Status -eq 'Up'); physical=[bool]$nic.HardwareInterface
  guid=$nic.InterfaceGuid; alias=$nic.Name} | ConvertTo-Json -Compress
"""


def inspect_link(destination):
    destination = str(address(destination))
    if sys.platform != "win32":
        raise TransportError("pi_route_unavailable")
    system = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0"
    script = base64.b64encode(_ROUTE_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ, ULAB_ROUTE_TARGET=destination, PSModulePath=str(system / "Modules"))
    try:
        result = subprocess.run(
            [str(system / "powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", script], cwd=system, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )
        return verified_link(json.loads(result.stdout.decode("utf-8-sig")), destination)
    except (subprocess.SubprocessError, OSError, ValueError):
        raise TransportError("pi_route_unavailable") from None


def best_index(destination):
    api = ctypes.WinDLL("iphlpapi.dll", winmode=0x800)
    query = api.GetBestInterface
    query.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    query.restype = ctypes.c_uint32
    index = ctypes.c_uint32()
    raw = int.from_bytes(socket.inet_aton(destination), "little")
    if query(raw, ctypes.byref(index)) != 0:
        raise TransportError("pi_route_unavailable")
    return index.value


async def open_gadget(link, destination, port):
    # Windows expects network-order bytes on set and a host-order DWORD on get.
    unicast_if = getattr(socket, "IP_UNICAST_IF", 31)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_IP, unicast_if, struct.pack("!I", link.index))
        sock.bind((link.source, 0))
        await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, (destination, port)), 5)
        if (sock.getsockname()[0] != link.source or sock.getpeername() != (destination, port)
                or sock.getsockopt(socket.IPPROTO_IP, unicast_if) != link.index
                or best_index(destination) != link.index):
            raise TransportError("pi_link_changed")
        streams = await asyncio.open_connection(sock=sock)
        sock = None  # ownership transferred to the stream transport
        return streams
    except (OSError, TimeoutError):
        raise TransportError("pi_relay_unavailable") from None
    finally:
        if sock is not None:
            sock.close()


class PiRelay:
    def __init__(self, settings, pairings):
        self.settings = settings
        self.pairings = pairings
        self.selection = None

    def peer_id(self):
        if not self.settings.reuse_direct:
            return RELAY_PEER
        try:
            direct = self.pairings.direct()
        except (OSError, PairingError):
            raise TransportError("pairing_store_failed") from None
        if direct is None:
            raise TransportError("pi_direct_pair_missing")
        return direct.device

    async def discover(self):
        self.selection = None
        self.settings.validate()
        link = await asyncio.to_thread(inspect_link, self.settings.destination)
        device = Device(self.peer_id(), f"{link.alias} / {link.source} → {self.settings.destination}")
        self.selection = (device, link)
        return [device]

    async def connect(self, device):
        if self.selection is None or self.selection[0] != device or self.peer_id() != device.stable_id:
            raise TransportError("pi_link_changed")
        link = await asyncio.to_thread(inspect_link, self.settings.destination)
        if link.guid != self.selection[1].guid:
            raise TransportError("pi_link_changed")
        return await open_gadget(link, self.settings.destination, self.settings.port)
