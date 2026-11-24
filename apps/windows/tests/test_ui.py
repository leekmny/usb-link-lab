import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from usblinklab.protocol import Mode
from usblinklab.window import Window


class Bridge(QObject):
    changed = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.commands = []

    def submit(self, name, *args):
        self.commands.append((name, *args))


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
