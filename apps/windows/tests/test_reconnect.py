"""Exercise reconnect policy over real authenticated loopback sessions."""

import asyncio
import json
import secrets
import time

import pytest
from PySide6.QtWidgets import QApplication

from usblinklab import controller as desktop
from usblinklab.controller import Controller, OutboxEntry
from usblinklab.pairing import Pairing, PairingStore
from usblinklab.protocol import Mode, ProtocolError, handshake
from usblinklab.session import Session, SessionError
from usblinklab.storage import Storage
from usblinklab.transport import Device, TransportError


class RecoveringUSB:
    device = Device("stable-iphone", "Synthetic USB iPhone")
    other = Device("other-iphone", "Other USB iPhone")

    def __init__(self, root, secret):
        self.storage = Storage(root)
        self.secret = secret
        self.attempts = []
        self.online = True
        self.partial_first = True
        self.partial_frame = asyncio.Event()

    async def discover(self):
        return [self.device, self.other]

    async def connect(self, device):
        self.attempts.append(device)
        if device != self.device or not self.online:
            raise TransportError("device_gone")

        async def serve(reader, writer):
            try:
                if self.partial_first and len(self.attempts) == 1:
                    await handshake(reader, writer, self.secret, client=False)
                    await self.partial_frame.wait()
                    writer.write(b"\x00")
                    await writer.drain()
                else:
                    await Session(reader, writer, self.secret, self.storage, lambda _: None, client=False).run()
            except (ProtocolError, SessionError, OSError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            return await asyncio.open_connection(*server.sockets[0].getsockname())
        finally:
            server.close()

    def drop(self):
        self.online = False
        self.partial_frame.set()


def wait_for(app, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    secret = secrets.token_bytes(32)
    usb = RecoveringUSB(tmp_path / "iphone", secret)
    root = tmp_path / "windows"
    pairing = Pairing(usb.device.stable_id, secret)
    PairingStore(root / "pairing.dpapi").save(pairing)
    storage = Storage(root / "storage")
    interrupted = storage.stage_text("interrupted preview", Mode.HMACSHA256)
    (root / "outbox.json").write_text(json.dumps([
        OutboxEntry(interrupted, pairing.binding, "interrupted").record(),
    ]), encoding="utf-8")
    monkeypatch.setattr(desktop, "RECONNECT_INITIAL_DELAY", 0.04, raising=False)
    monkeypatch.setattr(desktop, "RECONNECT_MAX_DELAY", 0.08, raising=False)
    worker = Controller(root, usb=usb)
    events = []
    worker.changed.connect(lambda topic, value: events.append((topic, value)))
    worker.start()
    wait_for(app, lambda: ("connection", "select_device") in events)
    try:
        yield app, worker, usb, events, interrupted
    finally:
        # Release the synthetic peer if the test failed before requesting a drop.
        worker.loop.call_soon_threadsafe(usb.partial_frame.set)
        worker.shutdown()
        app.processEvents()


def test_reconnects_same_device_after_a_truncated_stream_without_replaying_interrupted_item(client):
    app, worker, usb, events, interrupted = client
    worker.submit("connect", usb.device.stable_id)
    wait_for(app, lambda: ("connection", "connected") in events)
    worker.loop.call_soon_threadsafe(usb.drop)
    wait_for(app, lambda: ("connection", "reconnecting") in events and len(usb.attempts) >= 3)

    worker.submit("enqueue", usb.device.stable_id, Mode.AES256GCM, "queued during retry", ())
    wait_for(app, lambda: ("enqueue_result", True) in events)
    worker.loop.call_soon_threadsafe(setattr, usb, "online", True)
    wait_for(app, lambda: events.count(("connection", "connected")) == 2)
    wait_for(app, lambda: any(topic == "item" and value["status"] == "complete"
                              and value["text"] == "queued during retry" for topic, value in events))

    assert set(usb.attempts) == {usb.device}
    assert [row.text for row in usb.storage.received()] == ["queued during retry"]
    assert interrupted.path.is_file()
    assert any(topic == "item" and value["id"] == interrupted.id and value["status"] == "interrupted"
               for topic, value in events)


def test_user_disconnect_stops_reconnect_while_peer_is_absent(client):
    app, worker, usb, events, _ = client
    worker.submit("connect", usb.device.stable_id)
    wait_for(app, lambda: ("connection", "connected") in events)
    worker.loop.call_soon_threadsafe(usb.drop)
    wait_for(app, lambda: ("connection", "reconnecting") in events)
    worker.submit("disconnect")
    wait_for(app, lambda: ("connection", "disconnected") in events and worker.connection.done())
    attempts = len(usb.attempts)
    worker.loop.call_soon_threadsafe(setattr, usb, "online", True)

    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.12), worker.loop).result(1)
    assert len(usb.attempts) == attempts
    assert events.count(("connection", "connected")) == 1


def test_first_unsuccessful_connect_is_not_retried(client):
    app, worker, usb, events, _ = client
    worker.loop.call_soon_threadsafe(setattr, usb, "online", False)
    worker.submit("connect", usb.device.stable_id)
    wait_for(app, lambda: ("connection", "device_gone") in events and worker.connection.done())
    assert usb.attempts == [usb.device]
    assert ("connection", "reconnecting") not in events


def test_local_staging_failure_does_not_reconnect(client):
    app, worker, usb, events, interrupted = client
    interrupted.path.unlink()
    worker.loop.call_soon_threadsafe(setattr, usb, "partial_first", False)
    worker.submit("retry", interrupted.id, usb.device.stable_id)
    wait_for(app, lambda: ("connection", "session_failed") in events and worker.connection.done())

    assert usb.attempts == [usb.device]
    assert ("connection", "reconnecting") not in events
    assert usb.storage.received() == []
