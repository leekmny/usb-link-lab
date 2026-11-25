"""Exercise the private capture scope, quota and HTTP access boundaries."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "capture"))
sys.path.insert(0, str(ROOT / "apps" / "raspberrypi"))
import ulab_capture as cap


class CaptureScope(unittest.TestCase):
    def setUp(self):
        self.info = {"gadget": {"interface": "test0", "filter": "host 192.0.2.2 and tcp port 43123"},
                     "loopback": {"interface": "lo", "filter": "tcp port 43124"},
                     "usbmon": {"interface": "usbmon3", "filter": "link[11] = 2"}}

    def test_user_filter_can_only_narrow_the_lab_filter(self):
        interface, expression = cap.scope(self.info, "gadget", "tcp.len > 1 or ip", False)
        self.assertEqual(interface, "test0")
        self.assertTrue(expression.startswith("(host 192.0.2.2 and tcp port 43123) and ("))
        with self.assertRaises(ValueError):
            cap.scope(self.info, "gadget", ") or ip or (tcp", False)
        with self.assertRaises(ValueError):
            cap.scope(self.info, "any", "", False)

    def test_usb_diagnostics_are_explicit_and_device_bound(self):
        with self.assertRaises(ValueError):
            cap.scope(self.info, "usbmon", "", False)
        self.assertEqual(cap.scope(self.info, "usbmon", "", True), ("usbmon3", "link[11] = 2"))

    def test_per_session_disk_and_time_bounds(self):
        for value in (0, 121, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cap.bounded(value, 120)
        self.assertEqual(cap.bounded(120, 120), 120)

    def test_capture_list_ignores_links_and_non_session_names(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / ("a" * 32 + ".pcapng")).write_bytes(b"x")
            (path / "personal.pcapng").write_bytes(b"x")
            self.assertEqual([p.name for p in cap.files(path)], ["a" * 32 + ".pcapng"])


if __name__ == "__main__":
    unittest.main()
