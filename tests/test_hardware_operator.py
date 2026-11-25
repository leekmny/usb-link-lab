"""Privacy boundaries for physical endpoint evidence. All inputs are synthetic."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


class OperatorPrivacyTests(unittest.TestCase):
    def test_private_receipt_uses_metadata_and_forbids_capture_export(self):
        import hardware_lab
        self.assertTrue(hasattr(hardware_lab, "ReceiveAudit"))
        logs = []
        audit = hardware_lab.ReceiveAudit({"a" * 64}, lambda name, **v: logs.append((name,v)),
                                          private_receipt=True)
        with tempfile.TemporaryDirectory() as root:
            meta = Path(root) / "metadata.json"
            # No content file exists. The observer must never reopen image contents.
            meta.write_text(json.dumps({"sha256":"b"*64, "name":"do-not-log.jpg"}))
            item = SimpleNamespace(direction="incoming", status="receiving", item_id=b"test",
                                   path=Path(root)/"content", kind="image", size=32,
                                   mode=SimpleNamespace(name="AES256GCM"))
            audit.observe(item)
            self.assertFalse(audit.capture_safe())
            item.status = "complete"
            audit.observe(item)
            self.assertTrue(audit.received.is_set())
            self.assertFalse(audit.capture_safe())
            self.assertEqual(logs[-1][0],"private_return_committed")
            self.assertNotIn("do-not-log",str(logs))
            self.assertEqual(logs[-1][1]["sha256"], "b"*64)

    def test_partial_or_unmatched_records_suppress_capture_downloads(self):
        import hardware_lab
        self.assertTrue(hasattr(hardware_lab, "ReceiveAudit"))
        audit=hardware_lab.ReceiveAudit({"a"*64},lambda *a,**k:None,private_receipt=False)
        self.assertTrue(audit.capture_safe())
        with tempfile.TemporaryDirectory() as root:
            meta=Path(root)/"metadata.json"
            meta.write_text(json.dumps({"sha256":"a"*64}))
            item=SimpleNamespace(direction="incoming",status="receiving",item_id=b"1",path=Path(root)/"content",
                                 kind="file",size=32,mode=SimpleNamespace(name="PLAINTEXT"))
            audit.observe(item)
            self.assertFalse(audit.capture_safe())
            item.status="complete"
            audit.observe(item)
            self.assertTrue(audit.capture_safe())
            item.item_id=b"2"; item.status="interrupted"
            audit.observe(item)
            self.assertFalse(audit.capture_safe())
