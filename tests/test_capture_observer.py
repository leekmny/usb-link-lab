"""A passive summary, including coalesced records, using actual TShark on public vectors."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "capture"))
from test_dissector import fixture
import ulab_capture as cap


class CaptureObserver(unittest.TestCase):
    def test_modes_and_sampled_item_counts_are_frame_not_packet_counts(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ("a" * 32 + ".pcapng")
            fixture(path)
            summary = cap.frame_summary(path, 43123)
            self.assertEqual(summary["status"], "observed")
            for by_dir in summary["frames"]:
                self.assertEqual(by_dir, {"c2s": 6, "s2c": 6})
            self.assertEqual(summary["authentication"], "unverified")
            self.assertTrue(1 <= len(summary["items"]) <= 6)
            for item in summary["items"]:
                self.assertEqual(set(item), {"local_id", "mode", "direction", "unix_ms", "data_bytes", "end_seen"})
                self.assertTrue(item["end_seen"])
                self.assertIn(item["data_bytes"], (13, 70))
            encoded = json.dumps(summary)
            self.assertNotIn("000102030405060708090a0b0c0d0e0f", encoded)
            self.assertNotIn("ULAB0001", encoded)
            self.assertLess(len(encoded), 2400)

    def test_malformed_and_packet_budget_have_explicit_partial_status(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ("b" * 32 + ".pcapng")
            fixture(path, "length")
            self.assertEqual(cap.frame_summary(path, 43123)["status"], "partial")
            fixture(path)
            with patch.object(cap, "OBSERVER_MAX_PACKETS", 8):
                self.assertEqual(cap.frame_summary(path, 43123)["status"], "limited")

    def test_user_stop_keeps_saved_capture_and_records_manual_reason(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cap, "STATE", Path(d)):
            sid = "e"*32
            path = Path(d)/(sid+".pcapng")
            fixture(path)
            process = Mock(returncode=0)
            process.poll.return_value = None
            process.communicate.return_value = (None, b"")
            record = {"id":sid, "result":"capturing", "layer":"gadget", "filter":"tcp port 43123"}
            with patch.dict(cap.ACTIVE, {sid:process}), patch.object(cap, "OBSERVE_QUEUE", cap.queue.Queue()):
                cap.stop(sid)
                process.send_signal.assert_called_once_with(cap.signal.SIGINT)
                cap.observe(process, sid, record, path)
                self.assertEqual(cap.OBSERVE_QUEUE.get_nowait(), (sid, 43123))
            saved = json.loads(path.with_suffix(".json").read_bytes())
            self.assertEqual(saved["result"], "complete")
            self.assertEqual(saved["completion_reason"], "user_stopped")
            self.assertEqual(saved["observation"]["status"], "pending")

    def test_reboot_retains_interruption_and_no_invented_end_time(self):
        with tempfile.TemporaryDirectory() as d, patch.object(cap, "STATE", Path(d)):
            sid = "d"*32
            (Path(d)/(sid+".pcapng")).write_bytes(b"partial")
            cap.write_meta(sid, {"id":sid, "result":"capturing", "layer":"gadget"})
            cap.recover_observations()
            meta = json.loads((Path(d)/(sid+".json")).read_bytes())
            self.assertEqual(meta["result"], "interrupted")
            self.assertIn("interrupted_detected_utc", meta)
            self.assertIsNone(meta.get("ended_utc"))
            self.assertEqual(meta["observation"]["status"], "interrupted")
