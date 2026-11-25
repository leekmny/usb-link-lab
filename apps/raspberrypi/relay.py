#!/usr/bin/env python3
"""Single-session, opaque TCP relay for the ULAB USB lab."""
import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

import gadget


BUFFER = 64 * 1024
CONNECT_TIMEOUT = 5
WRITE_TIMEOUT = 60
HALF_CLOSE_TIMEOUT = 10


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def event(name, **values):
    print(json.dumps({"ts": utc_now(), "event": name, **values}), flush=True)


@dataclass(frozen=True)
class Settings:
    interface: str
    listen_address: str
    windows_address: str
    listen_port: int
    upstream_port: int

    @classmethod
    def parse(cls, values):
        config = cls(**values)
        if not gadget.IFACE_NAME.fullmatch(config.interface):
            raise ValueError("invalid interface")
        for field in (config.listen_address, config.windows_address):
            address = IPv4Address(field)
            if (str(address) != field or address.is_unspecified or address.is_loopback
                    or address.is_multicast or address.is_reserved):
                raise ValueError("invalid unicast IPv4 address")
        if config.listen_address == config.windows_address:
            raise ValueError("peer and gadget must have different addresses")
        for port in (config.listen_port, config.upstream_port):
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError("invalid unprivileged port")
        if config.upstream_port == config.listen_port:
            raise ValueError("select distinct frontend and upstream ports")
        return config

    @classmethod
    def load(cls, path):
        return cls.parse(json.loads(Path(path).read_bytes()))


class Relay:
    def __init__(self, config, status, half_close_timeout=HALF_CLOSE_TIMEOUT, check_upstream=None):
        self.config = config
        self.status = Path(status)
        self.half_close_timeout = half_close_timeout
        self.check_upstream = check_upstream
        self.task = None
        self.stopping = False
        self.sessions_started = self.sessions_closed = self.sessions_rejected = 0
        self.counts = {"bytes_windows_to_iphone": 0, "bytes_iphone_to_windows": 0}
        self.last_result = None

    def publish(self):
        state = "stopping" if self.stopping else "connected" if self.task else "listening"
        snapshot = {
            "updated_utc": utc_now(), "state": state, "interface": self.config.interface,
            "listen_address": self.config.listen_address, "listen_port": self.config.listen_port,
            "upstream_port": self.config.upstream_port, "active_sessions": int(self.task is not None),
            "sessions_started": self.sessions_started, "sessions_closed": self.sessions_closed,
            "sessions_rejected": self.sessions_rejected, "last_result": self.last_result,
            **self.counts,
        }
        temporary = self.status.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        temporary.chmod(0o644)
        os.replace(temporary, self.status)

    def accept(self, reader, writer):
        peer = writer.get_extra_info("peername")
        if self.stopping or self.task is not None or not peer or peer[0] != self.config.windows_address:
            self.sessions_rejected += 1
            writer.close()
            return
        self.sessions_started += 1
        self.task = asyncio.create_task(self.session(reader, writer))

    async def pump(self, reader, writer, counter, local_counts):
        while True:
            block = await reader.read(BUFFER)
            if not block:
                writer.write_eof()
                await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)
                return
            writer.write(block)
            await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)
            self.counts[counter] += len(block)
            local_counts[counter] += len(block)

    async def session(self, windows_reader, windows_writer):
        local_id = uuid.uuid4().hex
        result = "cancelled"
        writers = [windows_writer]
        tasks = []
        local_counts = dict.fromkeys(self.counts, 0)
        event("accepted", session=local_id)
        try:
            if self.check_upstream and not await asyncio.to_thread(self.check_upstream):
                result = "upstream_unavailable"
                return
            try:
                phone_reader, phone_writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.config.upstream_port, limit=BUFFER),
                    CONNECT_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                result = "upstream_unavailable"
                return
            writers.append(phone_writer)
            for writer in writers:
                writer.transport.set_write_buffer_limits(high=BUFFER, low=BUFFER // 4)
            tasks = [
                asyncio.create_task(self.pump(windows_reader, phone_writer,
                                               "bytes_windows_to_iphone", local_counts)),
                asyncio.create_task(self.pump(phone_reader, windows_writer,
                                               "bytes_iphone_to_windows", local_counts)),
            ]
            finished, remaining = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for done in finished:
                done.result()
            if remaining:
                await asyncio.wait_for(asyncio.gather(*remaining), self.half_close_timeout)
            result = "closed"
        except asyncio.TimeoutError:
            result = "progress_timeout"
        except (OSError, RuntimeError):
            result = "transport_error"
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for writer in writers:
                writer.close()
            try:
                await asyncio.wait_for(asyncio.gather(*(w.wait_closed() for w in writers),
                                                        return_exceptions=True), 2)
            except asyncio.TimeoutError:
                for writer in writers:
                    writer.transport.abort()
            self.last_result = result
            self.sessions_closed += 1
            self.task = None
            event("closed", session=local_id, result=result, **local_counts)

    async def wait_idle(self):
        if self.task:
            await asyncio.shield(self.task)

    async def stop(self):
        self.stopping = True
        # Let a just-accepted session enter its cleanup block before cancellation.
        await asyncio.sleep(0)
        if self.task:
            task = self.task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.publish()


async def run(config, status):
    relay = Relay(config, status,
                  check_upstream=lambda: gadget.iproxy_owns_port(config.upstream_port))
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    sock = gadget.listener(config)
    try:
        server = await asyncio.start_server(relay.accept, sock=sock, limit=BUFFER, backlog=4)
    except BaseException:
        sock.close()
        raise
    try:
        async with server:
            event("listening", interface=config.interface, port=config.listen_port)
            while not shutdown.is_set():
                relay.publish()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(shutdown.wait(), 2)
                if not shutdown.is_set():
                    await asyncio.to_thread(gadget.validate_path, config)
    finally:
        await relay.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/ulab/relay.json"))
    parser.add_argument("--status", type=Path, default=Path("/run/ulab-relay/status.json"))
    args = parser.parse_args()
    try:
        asyncio.run(run(Settings.load(args.config), args.status))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        event("unavailable", reason=type(exc).__name__, errno=getattr(exc, "errno", None))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
