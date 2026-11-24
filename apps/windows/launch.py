"""Start the Windows desktop endpoint or check a packaged runtime in isolation."""

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from PySide6.QtCore import QLockFile, qVersion
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication, QMessageBox

from usblinklab import PROTOCOL_REVISION, __version__
from usblinklab.controller import Controller
from usblinklab.protocol import Mode
from usblinklab.window import Window


def smoke(app, marker):
    with TemporaryDirectory(prefix="USBLinkLab-smoke-") as scratch:
        root = Path(scratch)
        controller = Controller(root)
        window = Window(controller, root)
        window.show()
        app.processEvents()
        try:
            modes = [window.mode.itemData(index) for index in range(window.mode.count())]
            if set(modes) != set(Mode) or window.mode.currentData() != Mode.AES256GCM:
                raise RuntimeError("Protection selector failed to initialize")
            if window.theme != "light" or window.items.topLevelItemCount() != 0 or window.grab().isNull():
                raise RuntimeError("The Windows conversation view did not render")
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({
                "app": "USBLinkLab", "app_version": __version__, "protocol": PROTOCOL_REVISION,
                "qt": qVersion(), "platform": app.platformName(), "theme": window.theme,
                "frozen": bool(getattr(sys, "frozen", False)), "status": "passed",
            }) + "\n", encoding="utf-8")
        finally:
            window.close()


def main():
    parser = argparse.ArgumentParser(description="USBLinkLab Windows desktop app")
    parser.add_argument("--smoke-test", type=Path, metavar="MARKER")
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("USBLinkLab")
    app.setStyle("Fusion")
    QImageReader.setAllocationLimit(64)
    if args.smoke_test is not None:
        smoke(app, args.smoke_test)
        return 0

    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "USBLinkLab"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        QMessageBox.critical(None, "USBLinkLab", "로컬 저장소를 열 수 없습니다. Unable to open local storage.")
        return 1
    lock = QLockFile(str(root / "active.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.warning(None, "USBLinkLab", "이미 실행 중입니다. USBLinkLab is already running.")
        return 1
    controller = Controller(root)
    try:
        window = Window(controller, root)
        window.show()
        controller.start()
        return app.exec()
    finally:
        controller.shutdown()
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
