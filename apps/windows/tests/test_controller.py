"""Exercise the desktop worker with an isolated Python loopback peer."""

import asyncio
import base64
from pathlib import Path
import secrets
import time

from PySide6.QtWidgets import QApplication

from usblinklab.controller import Controller
from usblinklab.pairing import Pairing, PairingStore
from usblinklab.pin_pairing import PairingProtocolError, pair_server
from usblinklab.protocol import Mode, ProtocolError
from usblinklab.session import Session, SessionError
from usblinklab.storage import Storage
from usblinklab.transport import Device


class SyntheticUSB:
    device = Device("local-fixture", "Synthetic USB peer")

    def __init__(self, root, secret):
        self.storage = Storage(root)
        self.secret = secret
        self.attempts = 0
        self.code = None

    async def discover(self):
        return [self.device]

    async def connect(self, device):
        assert device == self.device
        self.attempts += 1

        async def serve(reader, writer):
            try:
                code, self.code = self.code, None
                if code is not None:
                    self.secret = await pair_server(reader, writer, code)
                await Session(reader, writer, self.secret, self.storage, lambda _: None, client=False).run()
            except (PairingProtocolError, ProtocolError, SessionError):
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
    root = tmp_path / "client"
    PairingStore(root / "pairing.dpapi").save(Pairing(usb.device.stable_id, secret))
    controller = Controller(root, usb=usb)
    notifications = []
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        wait_for(lambda: ("connection", "select_device") in notifications)
        device = usb.device.stable_id
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
    PairingStore(root / "pairing.dpapi").save(Pairing(usb.device.stable_id, secret))
    controller = Controller(root, usb=usb)
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        wait_for(lambda: ("connection", "select_device") in notifications)
        device = usb.device.stable_id
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


def test_qr_enrollment_waits_for_existing_v1_authentication_then_saves(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "client"
    usb = SyntheticUSB(tmp_path / "peer", None)
    store = PairingStore(root / "pairing.dpapi")
    controller = Controller(root, usb=usb)
    notifications = []
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        device = usb.device.stable_id
        wait_for(lambda: ("connection", "select_device") in notifications)
        controller.submit("new_pair_qr", device, "old")
        controller.submit("cancel_pair_code", device, "old")
        controller.submit("new_pair_qr", device, "fresh")
        wait_for(lambda: any(name == "pairing_qr" and data["request_id"] == "fresh"
                             for name, data in notifications))
        invitation = next(data for name, data in notifications
                          if name == "pairing_qr" and data["request_id"] == "fresh")
        assert invitation["device"] == device and invitation["ttl"] == 120
        assert store.load(device) is None
        prefix, payload = invitation["payload"].split(":")
        assert prefix == "ULABQR1"
        usb.secret = base64.urlsafe_b64decode(payload + "=")
        controller.submit("pair_qr", device, "old")
        controller.submit("pair_qr", device, "fresh")
        wait_for(lambda: ("connection", "connected") in notifications)
        assert usb.attempts == 1 and store.load(device).secret == usb.secret
    finally:
        controller.shutdown()
        app.processEvents()


def test_failed_six_digit_attempt_is_consumed_and_keeps_existing_credential(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "client"
    secret = secrets.token_bytes(32)
    usb = SyntheticUSB(tmp_path / "peer", secret)
    store = PairingStore(root / "pairing.dpapi")
    store.save(Pairing(usb.device.stable_id, secret))
    controller = Controller(root, usb=usb)
    notifications = []
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        device = usb.device.stable_id
        wait_for(lambda: ("connection", "select_device") in notifications)
        controller.submit("new_pair_code", device, "first")
        wait_for(lambda: any(name == "pairing_code" for name, _ in notifications))
        original = next(data for name, data in notifications if name == "pairing_code")["code"]
        assert len(original) == 6 and original.isascii() and original.isdecimal()
        usb.code = f"{(int(original) + 1) % 1000000:06d}"
        controller.submit("pair_code", device, "first")
        wait_for(lambda: ("connection", "pairing_code_failed") in notifications)
        assert store.load(device).secret == secret

        controller.submit("pair_code", device, "first")
        controller.submit("new_pair_code", device, "second")
        wait_for(lambda: any(name == "pairing_code" and data["request_id"] == "second"
                             for name, data in notifications))
        assert usb.attempts == 1
        usb.code = next(data for name, data in notifications
                        if name == "pairing_code" and data["request_id"] == "second")["code"]
        controller.submit("pair_code", device, "second")
        wait_for(lambda: ("connection", "connected") in notifications)
        assert usb.attempts == 2 and store.load(device).secret == usb.secret != secret
    finally:
        controller.shutdown()
        app.processEvents()


def test_qr_authenticated_but_key_save_failed_does_not_activate_session(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "client"
    old_secret = secrets.token_bytes(32)
    usb = SyntheticUSB(tmp_path / "peer", old_secret)
    store = PairingStore(root / "pairing.dpapi")
    store.save(Pairing(usb.device.stable_id, old_secret))

    def fail_to_save(_store, _candidate):
        raise OSError("synthetic DPAPI record write failure")

    monkeypatch.setattr(PairingStore, "save", fail_to_save)
    controller = Controller(root, usb=usb)
    notifications = []
    controller.changed.connect(lambda name, value: notifications.append((name, value)))
    controller.start()
    try:
        device = usb.device.stable_id
        wait_for(lambda: ("connection", "select_device") in notifications)
        controller.submit("new_pair_qr", device, "store-failure")
        wait_for(lambda: any(topic == "pairing_qr" for topic, _ in notifications))
        payload = next(value["payload"] for topic, value in notifications if topic == "pairing_qr")
        usb.secret = base64.urlsafe_b64decode(payload.split(":")[1] + "=")
        controller.submit("pair_qr", device, "store-failure")
        wait_for(lambda: ("connection", "pairing_store_failed") in notifications)
        assert ("connection", "connected") not in notifications
        assert store.load(device).secret == old_secret
    finally:
        controller.shutdown()
        app.processEvents()


def test_worker_deletes_a_restored_received_message(tmp_path):
    from hashlib import sha256

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "client"
    store = Storage(root / "storage")
    payload = b"received text"
    draft = store.begin_receive(len(payload))
    draft.write(payload)
    item = store.commit_receive(
        draft, mode=Mode.AES256GCM, kind="text", name="", media_type="text/plain; charset=utf-8",
        size=len(payload), sha256=sha256(payload).hexdigest(),
    )
    usb = SyntheticUSB(tmp_path / "peer", secrets.token_bytes(32))
    controller = Controller(root, usb=usb)
    seen = []
    controller.changed.connect(lambda name, value: seen.append((name, value)))
    controller.start()
    try:
        row_id = "in:" + item.id
        wait_for(lambda: any(topic == "item" and value["id"] == row_id for topic, value in seen))
        controller.submit("delete_received_message", row_id)
        wait_for(lambda: ("removed", row_id) in seen)
        assert store.received() == []
        assert not item.path.exists()
    finally:
        controller.shutdown()
