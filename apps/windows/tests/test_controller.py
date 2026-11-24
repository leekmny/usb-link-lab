"""Exercise the desktop worker with an isolated Python loopback peer."""

import asyncio
from pathlib import Path
import secrets
import time

from PySide6.QtWidgets import QApplication

from usblinklab.controller import Controller
from usblinklab.protocol import Mode
from usblinklab.session import Session, SessionError
from usblinklab.storage import Storage
from usblinklab.transport import Device


class SyntheticUSB:
    device = Device("local-fixture", "Synthetic USB peer")

    def __init__(self, root, secret):
        self.storage = Storage(root)
        self.secret = secret
        self.attempts = 0

    async def discover(self):
        return [self.device]

    async def connect(self, device):
        assert device == self.device
        self.attempts += 1

        async def serve(reader, writer):
            try:
                await Session(reader, writer, self.secret, self.storage, lambda _: None, client=False).run()
            except SessionError:
                pass

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            return await asyncio.open_connection(*server.sockets[0].getsockname())
        finally:
            server.close()


def wait_for(predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_desktop_worker_forwards_immutable_queued_modes(tmp_path):
    app = QApplication.instance() or QApplication([])
    secret = secrets.token_bytes(32)
    usb = SyntheticUSB(tmp_path / "peer", secret)
    controller = Controller(tmp_path / "client", usb=usb)
    notifications = []
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        wait_for(lambda: ("connection", "select_device") in notifications)
        device = usb.device.stable_id
        controller.submit("pair", device, secret.hex())
        wait_for(lambda: ("connection", "paired") in notifications)
        controller.submit("enqueue", device, Mode.AES256GCM, "첫 번째", ())
        wait_for(lambda: notifications.count(("enqueue_result", True)) == 1)
        controller.submit("enqueue", device, Mode.PLAINTEXT, "두 번째", ())
        wait_for(lambda: notifications.count(("enqueue_result", True)) == 2)
        controller.submit("connect", device)
        wait_for(lambda: sum(topic == "item" and value["status"] == "complete"
                             for topic, value in notifications) == 2)

        committed = {row.text: row.mode for row in usb.storage.received()}
        assert committed == {"첫 번째": Mode.AES256GCM, "두 번째": Mode.PLAINTEXT}
        assert usb.attempts == 1
    finally:
        controller.shutdown()
        app.processEvents()


def test_outbox_rejects_33rd_item_and_restores_on_restart(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "client"
    secret = secrets.token_bytes(32)
    usb = SyntheticUSB(tmp_path / "peer", secret)
    files = []
    for index in range(31):
        path = Path(tmp_path / f"item-{index}.bin")
        path.write_bytes(bytes([index]))
        files.append((path, "file"))
    notifications = []
    controller = Controller(root, usb=usb)
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        wait_for(lambda: ("connection", "select_device") in notifications)
        device = usb.device.stable_id
        controller.submit("pair", device, secret.hex())
        wait_for(lambda: ("connection", "paired") in notifications)
        controller.submit("enqueue", device, Mode.HMACSHA256, "queue limit", tuple(files))
        wait_for(lambda: any(topic == "enqueue_result" for topic, _ in notifications))
        assert ("enqueue_result", True) in notifications, [(topic, value) for topic, value in notifications if topic in {"error", "enqueue_result"}]
        controller.submit("enqueue", device, Mode.PLAINTEXT, "over the limit", ())
        wait_for(lambda: ("error", "queue_full") in notifications)
        assert sum(topic == "item" and value["status"] == "queued" for topic, value in notifications) == 32
    finally:
        controller.shutdown()

    notifications.clear()
    restored = Controller(root, usb=usb)
    restored.changed.connect(lambda name, value: notifications.append((name, value)))
    restored.start()
    try:
        wait_for(lambda: ("connection", "select_device") in notifications)
        assert sum(topic == "item" and value["status"] == "paused"
                   and value["mode"] == Mode.HMACSHA256 for topic, value in notifications) == 32
        assert usb.attempts == 0
    finally:
        restored.shutdown()
        app.processEvents()
