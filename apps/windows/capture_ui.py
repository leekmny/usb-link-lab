"""Capture the native Windows app with isolated data for UI testing."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from usblinklab.controller import Controller
from usblinklab.pi_transport import PathSettings
from usblinklab.protocol import Mode
from usblinklab.window import Window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("scenario", choices=["prerequisite", "light-narrow", "synthetic-active",
                                             "relay-route", "relay-settings"])
    parser.add_argument("--gadget-address", help="Confirmed IPv4 address on the Pi's USB gadget")
    parser.add_argument("--gadget-port", default=43123, type=int)
    args = parser.parse_args()
    relay = args.scenario.startswith("relay-")
    if relay and not args.gadget_address:
        parser.error("relay capture requires --gadget-address")
    app = QApplication([])
    app.setStyle("Fusion")
    if app.platformName() != "windows":
        raise SystemExit("For native Windows screenshots, use the Windows Qt platform.")
    with TemporaryDirectory(prefix="USBLinkLab-ui-") as scratch:
        root = Path(scratch)
        if args.scenario == "light-narrow":
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("theme", "light")
            settings.setValue("large", True)
            settings.setValue("language", 1)
            settings.sync()
        if relay:
            PathSettings("relay", args.gadget_address, args.gadget_port).save(root)
        bridge = Controller(root)
        window = Window(bridge, root)
        if args.scenario == "light-narrow":
            window.resize(800, 610)
        window.show()
        try:
            if args.scenario == "synthetic-active":
                window.resize(1190, 760)
                window.title.setText("USB 송수신 · 합성 테스트")
                window._update("devices", [("synthetic-device", "iPhone · 합성 데이터")])
                window.devices.setCurrentIndex(1)
                window._update("connection", "connected")
                common = {"path": None, "text": None, "kind": "file"}
                window._set_row({
                    **common, "id": "demo-1", "name": "usb-test-message.txt",
                    "mode": Mode.PLAINTEXT, "direction": "incoming", "text": "합성 테스트 메시지",
                    "status": "complete", "size": 32, "transferred": 32,
                })
                window._set_row({
                    **common, "id": "demo-2", "name": "synthetic-sample.bin",
                    "mode": Mode.AES256GCM, "direction": "outgoing", "status": "sending",
                    "size": 104857600, "transferred": 60817408,
                })
                window.items.setCurrentItem(window.items.topLevelItem(1))
                window._open_details()
            else:
                bridge.start()
                deadline = time.monotonic() + 15
                while window.connection_state in {"disconnected", "scanning"} and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.02)
                if window.connection_state in {"disconnected", "scanning"}:
                    raise RuntimeError("Native USB prerequisite scan did not finish")
                if relay:
                    if window.connection_state != "select_device" or window.devices.count() != 2:
                        raise RuntimeError(f"Could not verify the native gadget path: {window.connection_state}")
                    window.devices.setCurrentIndex(1)
                window.diagnostic_toggle.setChecked(True)
            app.processEvents()
            args.path.parent.mkdir(parents=True, exist_ok=True)
            if args.scenario == "relay-settings":
                captured = []
                def capture_dialog():
                    dialog = window.findChild(QDialog)
                    if dialog is not None:
                        captured.append(dialog.grab())
                        dialog.reject()
                QTimer.singleShot(150, capture_dialog)
                window._settings_dialog()
                if not captured:
                    raise RuntimeError("Native relay settings dialog did not open")
                image = captured[0]
            else:
                image = window.grab()
            if not image.save(str(args.path), "PNG"):
                raise RuntimeError("Could not save native Windows screenshot")
            print(f"{args.scenario}: {app.platformName()}, {window.connection_state}, "
                  f"scale={window.devicePixelRatioF():.2f}, {image.size()}")
        finally:
            window.close()
            bridge.shutdown()


if __name__ == "__main__":
    main()
