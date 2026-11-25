import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PI = Path(__file__).resolve().parents[1] / "apps" / "raspberrypi"
sys.path.insert(0, str(PI))


class ProvisionTests(unittest.TestCase):
    def test_dynamic_unit_uid_requires_private_systemd_runtime_directory(self):
        from gadget import directory_uid
        from types import SimpleNamespace
        self.assertEqual(directory_uid(SimpleNamespace(st_mode=0o40555, st_uid=62962)), 62962)
        for mode, uid in ((0o120777, 62962), (0o40777, 62962), (0o40555, 0), (0o100644, 62962)):
            self.assertIsNone(directory_uid(SimpleNamespace(st_mode=mode, st_uid=uid)))

    def test_only_usb_device_never_picks_first_from_multiple(self):
        self.assertIsNotNone(importlib.util.find_spec("configure"), "provisioning is missing")
        from configure import select_device
        a = "A" * 40
        b = "00008030-" + "B" * 16
        self.assertEqual(select_device([b], True), b)
        for devices in ([], [a, b], ["--network"], [" " + a]):
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                select_device(devices, True)

    def test_upstream_options_require_local_source_and_explicit_device(self):
        self.assertIsNotNone(importlib.util.find_spec("usb_proxy"), "USB launcher is missing")
        from usb_proxy import iproxy_argv
        udid = "00008030-" + "B" * 16
        usage = "--local --udid --source LOCAL_PORT:DEVICE_PORT"
        self.assertEqual(iproxy_argv(usage, udid, 53000),
                         ["/usr/bin/iproxy", "-l", "-u", udid, "-s", "127.0.0.1", "53000:43123"])
        for missing in usage.split():
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                iproxy_argv(usage.replace(missing, ""), udid, 53000)
        with self.assertRaises(ValueError):
            iproxy_argv(usage, " -n", 53000)

    def test_configuration_writes_selected_phone_to_private_file_only(self):
        self.assertIsNotNone(importlib.util.find_spec("configure"), "provisioning is missing")
        from configure import write_config
        from relay import Settings
        udid = "C" * 40
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = Settings("demo0", "192.0.2.1", "192.0.2.2", 43123, 46124)
            write_config(root, config, udid)
            self.assertEqual((root / "iphone.udid").read_text().strip(), udid)
            raw = (root / "relay.json").read_text()
            self.assertNotIn(udid, raw)
            self.assertEqual(json.loads(raw)["upstream_port"], 46124)
            if os.name == "posix":
                self.assertEqual((root / "iphone.udid").stat().st_mode & 0o777, 0o600)

    def test_malformed_config_is_rejected_with_no_value_in_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sample.json"
            config.write_text('{"private_unexpected_key": "synthetic-secret"}')
            app = PI / "relay.py"
            self.assertTrue(app.exists(), "relay is missing")
            run = subprocess.run([sys.executable, str(app), "--config", str(config)],
                                 capture_output=True, text=True, timeout=4)
            self.assertEqual(run.returncode, 1)
            self.assertNotIn("synthetic-secret", run.stdout + run.stderr)
            self.assertNotIn("private_unexpected_key", run.stdout + run.stderr)
            self.assertEqual(json.loads(run.stdout)["event"], "unavailable")
