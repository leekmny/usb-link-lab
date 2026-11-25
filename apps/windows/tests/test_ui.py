import os
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from usblinklab.protocol import Mode
from usblinklab.window import Window


class Bridge(QObject):
    changed = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.commands = []

    def submit(self, name, *args):
        self.commands.append((name, *args))


def open_pairing(window):
    # Exit the dialog's nested event loop if a blocking dialog is used.
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: window.findChild(QDialog).reject())
    watchdog.start(0)
    window.pair.click()
    watchdog.stop()
    return window.findChild(QDialog, "pairingDialog")


def pairing_button(dialog, key):
    return next(button for button in dialog.findChildren(QPushButton) if button.property("copyKey") == key)


def test_composer_snapshots_both_protection_modes(tmp_path):
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    window = Window(bridge, tmp_path)
    window.show()
    app.processEvents()
    bridge.changed.emit("devices", [("phone", "iPhone 1")])
    window.devices.setCurrentIndex(1)
    assert window.mode.currentData() == Mode.AES256GCM

    window.message.setPlainText("First")
    window.send.click()
    assert bridge.commands[-1] == ("enqueue", "phone", Mode.AES256GCM, "First", ())

    bridge.changed.emit("enqueue_result", True)
    window.mode.setCurrentIndex(window.mode.findData(Mode.PLAINTEXT))
    window.message.setPlainText("Second")
    assert window.exposure.isVisible()
    window.send.click()
    assert bridge.commands[-1] == ("enqueue", "phone", Mode.PLAINTEXT, "Second", ())
    assert bridge.commands[-2][2] == Mode.AES256GCM
    window.close()


def test_first_run_has_no_seed_messages(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window(Bridge(), tmp_path)
    window.show()
    app.processEvents()
    assert window.items.topLevelItemCount() == 0
    assert window.theme == "light"
    assert not window.send.isEnabled()
    assert window.empty.isVisible()
    window.close()


def test_pairing_defaults_to_qr_and_keeps_payload_out_of_text(tmp_path):
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    window = Window(bridge, tmp_path)
    window.show()
    bridge.changed.emit("devices", [("phone", "iPhone 1")])
    window.devices.setCurrentIndex(1)
    dialog = open_pairing(window)

    (command,) = bridge.commands
    assert command[:2] == ("new_pair_qr", "phone")
    request_id = command[2]
    start = pairing_button(dialog, "start_pair")
    pairing_qr = dialog.findChild(QLabel, "pairingQr")
    assert pairing_qr.isVisible()
    assert not dialog.findChild(QLabel, "pairingCode").isVisible()
    assert not start.isEnabled()

    payload = "ULABQR1:synthetic-qr-fixture"
    bridge.changed.emit("pairing_qr", {"device": "phone", "payload": payload, "expires_at": monotonic() + 120, "request_id": request_id})
    assert start.isEnabled()
    image = pairing_qr.pixmap().toImage()
    assert not image.isNull()
    assert image.pixelColor(0, 0).name() == "#ffffff"
    assert image.pixelColor(image.width() // 5, image.height() // 5).name() == "#000000"
    assert all(payload not in widget.text() for widget in window.findChildren(QLabel))

    start.click()
    assert bridge.commands == [command, ("pair_qr", "phone", request_id)]
    assert pairing_qr.pixmap().isNull()
    assert not window.pair.isEnabled()
    bridge.changed.emit("pairing_request_error", {
        "device": "phone", "request_id": request_id, "reason": "pairing_code_expired",
    })
    assert window.pair.isEnabled()
    window.close()
    app.processEvents()


def test_pairing_displays_only_selected_device_code_and_starts_once(tmp_path):
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    window = Window(bridge, tmp_path)
    window.show()
    bridge.changed.emit("devices", [("phone", "iPhone 1"), ("other", "iPhone 2")])
    window.devices.setCurrentIndex(1)
    dialog = open_pairing(window)
    pairing_button(dialog, "use_pair_code").click()

    initial, command = bridge.commands
    assert initial[:2] == ("new_pair_qr", "phone")
    assert command[:2] == ("new_pair_code", "phone")
    request_id = command[2]
    code = dialog.findChild(QLabel, "pairingCode")
    start = pairing_button(dialog, "start_pair")
    assert not start.isEnabled()
    bridge.changed.emit("pairing_code", {
        "device": "other", "code": "123456", "expires_at": monotonic() + 120, "request_id": request_id,
    })
    assert not start.isEnabled()
    bridge.changed.emit("pairing_code", {
        "device": "phone", "code": "004287", "expires_at": monotonic() + 120, "request_id": request_id,
    })
    assert code.text() == "004287"
    assert not code.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert "004287" not in window.diagnostic_text.text()

    start.click()
    assert bridge.commands == [initial, command, ("pair_code", "phone", request_id)]
    assert not dialog.isVisible()
    assert code.text() == ""
    assert not window.pair.isEnabled()
    assert not window.refresh.isEnabled()
    bridge.changed.emit("connection", "pairing_code_failed")
    assert window.pair.isEnabled()
    assert window.connection_status.text() == window.tr_key("pairing_code_failed")

    window.connect.click()
    assert bridge.commands[-1] == ("connect", "phone")
    window.close()
    app.processEvents()


def test_pairing_refresh_hides_stale_code_and_escape_cancels(tmp_path):
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    window = Window(bridge, tmp_path)
    window.show()
    bridge.changed.emit("devices", [("phone", "iPhone 1")])
    window.devices.setCurrentIndex(1)
    dialog = open_pairing(window)
    pairing_button(dialog, "use_pair_code").click()

    initial, command = bridge.commands
    assert command[:2] == ("new_pair_code", "phone")
    request_id = command[2]
    code = dialog.findChild(QLabel, "pairingCode")
    buttons = {button.property("copyKey"): button for button in dialog.findChildren(QPushButton)}
    previous = {"device": "phone", "code": "089012", "expires_at": monotonic() + 120, "request_id": request_id}
    bridge.changed.emit("pairing_code", previous)
    assert buttons["start_pair"].isEnabled()
    buttons["new_pair_code"].click()
    first, previous_code, refreshed = bridge.commands
    assert first == initial
    assert previous_code == command
    assert refreshed[:2] == ("new_pair_code", "phone")
    assert refreshed[2] != request_id
    assert not buttons["start_pair"].isEnabled()
    assert "089012" not in code.text()
    bridge.changed.emit("pairing_code", previous)
    assert not buttons["start_pair"].isEnabled()
    bridge.changed.emit("pairing_request_error", {
        "device": "phone", "request_id": request_id, "reason": "pairing_code_expired",
    })
    bridge.changed.emit("pairing_code", {**previous, "code": "034567", "request_id": refreshed[2]})
    assert code.text() == "034567"
    bridge.changed.emit("pairing_request_error", {
        "device": "phone", "request_id": refreshed[2], "reason": "pairing_code_expired",
    })
    assert not buttons["start_pair"].isEnabled()
    assert buttons["new_pair_code"].isEnabled()
    assert "034567" not in code.text()

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert bridge.commands[-1] == ("cancel_pair_code", "phone", refreshed[2])
    assert not dialog.isVisible()
    window.close()
    app.processEvents()


def test_received_message_delete_requires_confirmation_and_clears_preview(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    window = Window(bridge, tmp_path)
    window.show()
    app.processEvents()
    event = {
        "id": "in:0123456789abcdef0123456789abcdef", "direction": "incoming", "status": "complete",
        "mode": Mode.AES256GCM, "kind": "text", "name": "", "size": 10, "transferred": 10,
        "path": tmp_path / "content", "text": "saved text",
    }
    bridge.changed.emit("item", event)
    window.history.click()
    window.items.setCurrentItem(window.items.topLevelItem(0))
    window._open_details()

    assert window.preview.text() == "saved text"
    assert window.delete_message.isVisible()
    confirmations = []

    def reject(title, message):
        confirmations.append((title, message))
        return False

    monkeypatch.setattr(window, "_confirm", reject)
    window.delete_message.click()
    assert len(confirmations) == 1
    assert bridge.commands == []
    monkeypatch.setattr(window, "_confirm", lambda *_: True)
    window.delete_message.click()
    assert bridge.commands == [("delete_received_message", event["id"])]

    bridge.changed.emit("removed", event["id"])
    assert window.items.topLevelItemCount() == 0
    assert window.preview.text() == ""
    assert not window.delete_message.isVisible()

    bridge.changed.emit("item", {**event, "id": "in:file", "kind": "file", "text": None})
    window.items.setCurrentItem(window.items.topLevelItem(0))
    assert not window.delete_message.isVisible()
    window.close()
    app.processEvents()


def test_history_bulk_delete_selects_only_committed_messages(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QAbstractItemView

    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    window = Window(bridge, tmp_path)
    window.show()
    app.processEvents()
    event = {
        "direction": "incoming", "status": "complete", "mode": Mode.AES256GCM, "kind": "text",
        "name": "", "size": 10, "transferred": 10, "path": tmp_path / "content", "text": "saved text",
    }
    ids = ("in:first", "in:second", "in:image", "sent", "in:active")
    for row_id, changes in zip(ids, ({}, {}, {"kind": "image"}, {"direction": "outgoing"},
                                    {"status": "receiving"})):
        bridge.changed.emit("item", {**event, "id": row_id, **changes})

    assert not window.select_messages.isVisible()
    window.history.click()
    window.items.setCurrentItem(window.rows["sent"][0])
    window.select_messages.click()
    assert window.selecting_messages
    assert not window._send_shortcut.isEnabled()
    assert not window.delete_selected_messages.isEnabled()
    checkable = Qt.ItemFlag.ItemIsUserCheckable
    assert [bool(window.rows[row_id][0].flags() & checkable) for row_id in ids] == [
        True, True, False, False, False,
    ]
    first, second = (window.rows[row_id][0] for row_id in ids[:2])
    window.items.setCurrentItem(first)
    QTest.keyClick(window.items, Qt.Key.Key_Space)
    second.setCheckState(0, Qt.CheckState.Checked)
    assert first.checkState(0) == Qt.CheckState.Checked
    assert window.delete_selected_messages.isEnabled()
    assert "2" in window.selection_count.text()

    monkeypatch.setattr(window, "_confirm", lambda *_: False)
    window.delete_selected_messages.click()
    assert bridge.commands == []
    monkeypatch.setattr(window, "_confirm", lambda *_: True)
    window.delete_selected_messages.click()
    assert bridge.commands == [("delete_received_messages", ids[:2])]
    assert not window.delete_selected_messages.isEnabled()

    bridge.changed.emit("removed", ids[0])
    bridge.changed.emit("bulk_delete_result", {"removed": 1, "pending": 0, "failed": 1})
    assert window.selecting_messages
    assert "1" in window.selection_count.text()
    assert second.checkState(0) == Qt.CheckState.Checked
    assert window.delete_selected_messages.isEnabled()

    window.cancel_message_selection.click()
    assert window.items.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection
    assert window._selected()["id"] == "sent"
    assert window._send_shortcut.isEnabled()
    assert all(not window.rows[row_id][0].flags() & checkable for row_id in ids[1:])
    window.close()
    app.processEvents()
