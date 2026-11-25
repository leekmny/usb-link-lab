"""Opt-in, real Linux loopback capture with the production byte relay and two Python endpoints."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("ULAB_RUN_LIVE_CAPTURE") == "1",
                     "Opt in on Linux with capture permission using ULAB_RUN_LIVE_CAPTURE=1")
class ExperimentTests(unittest.TestCase):
    def test_live_clear_and_encrypted_images(self):
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "run"
            run = subprocess.run([sys.executable, str(ROOT / "tools/relay_experiment.py"),
                                  "--out", str(out), "--kind", "image", "--mode", "0", "--mode", "1"],
                                 capture_output=True, text=True, timeout=75)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((out / "comparison.json").read_bytes())
            self.assertEqual(report["transport"], "Linux loopback TCP")
            self.assertEqual(len(report["experiments"]), 2)
            source_hash = hashlib.sha256((out / "source.png").read_bytes()).hexdigest()
            for case in report["experiments"]:
                self.assertEqual(case["capture_sha256"],
                                 hashlib.sha256((out / case["capture"]).read_bytes()).hexdigest())
                self.assertEqual(case["source_sha256"], source_hash)
                self.assertEqual(set(case["received_sha256"].values()), {source_hash})
                self.assertEqual(len(case["received_sha256"]), 2)
                self.assertEqual(case["visible_in_data"], case["mode"] == 0)
                self.assertEqual(case["recovered_files"], 4 if case["mode"] == 0 else 0)
                self.assertGreater(case["captured_packets"], 0)
                self.assertEqual(set(case["legs"]), {"front", "upstream"})
                self.assertTrue(all(case["legs"][leg]["DATA"] == 2 for leg in case["legs"]))
