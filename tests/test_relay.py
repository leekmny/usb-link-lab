"""Loopback tests exercise relay transport only, with synthetic bytes."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PI = Path(__file__).resolve().parents[1] / "apps" / "raspberrypi"
sys.path.insert(0, str(PI))


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec("relay"), "Pi relay is missing")
        import relay
        self.relay_module = relay
        self.upstreams = asyncio.Queue()
        self.up = await asyncio.start_server(
            lambda r, w: self.upstreams.put_nowait((r, w)), "127.0.0.1", 0)
        self.tmp = tempfile.TemporaryDirectory()
        self.status = Path(self.tmp.name) / "status.json"
        config = relay.Settings("lab", "127.0.0.1", "127.0.0.1", 43123,
                                self.up.sockets[0].getsockname()[1])
        self.app = relay.Relay(config, self.status, half_close_timeout=0.5)
        self.front = await asyncio.start_server(self.app.accept, "127.0.0.1", 0)
        self.writers = []

    async def asyncTearDown(self):
        self.front.close()
        self.up.close()
        await self.app.stop()
        while not self.upstreams.empty():
            _, writer = self.upstreams.get_nowait()
            self.writers.append(writer)
        for writer in self.writers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in self.writers),
                             return_exceptions=True)
        await asyncio.gather(self.front.wait_closed(), self.up.wait_closed())
        self.tmp.cleanup()

    async def connect(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.front.sockets[0].getsockname()[1])
        self.writers.append(w)
        return r, w

    async def proxy_side(self):
        r, w = await asyncio.wait_for(self.upstreams.get(), 2)
        self.writers.append(w)
        return r, w

    async def test_fragmentation_order_bidirectional_and_final_ack_after_fin(self):
        down, windows = await self.connect()
        up, phone = await self.proxy_side()
        message = bytes(range(256)) * 700
        for part in (message[:3], message[3:1001], message[1001:]):
            windows.write(part)
            await windows.drain()
        phone.write(b"ULAB0001-server")
        await phone.drain()
        self.assertEqual(await asyncio.wait_for(up.readexactly(len(message)), 2), message)
        self.assertEqual(await asyncio.wait_for(down.readexactly(15), 2), b"ULAB0001-server")
        windows.write_eof()
        self.assertEqual(await asyncio.wait_for(up.read(1), 2), b"")
        phone.write(b"ack")
        phone.write_eof()
        self.assertEqual(await asyncio.wait_for(down.read(), 2), b"ack")
        await self.app.wait_idle()
        self.app.publish()
        status = json.loads(self.status.read_text(encoding="utf-8"))
        self.assertEqual(status["bytes_windows_to_iphone"], len(message))
        self.assertEqual(status["bytes_iphone_to_windows"], 18)
        self.assertEqual(status["active_sessions"], 0)
        self.assertNotIn("ack", self.status.read_text(encoding="utf-8"))

    async def test_second_client_rejected_and_stuck_half_close_releases_slot(self):
        _, first = await self.connect()
        _, phone = await self.proxy_side()
        other, _ = await self.connect()
        self.assertEqual(await asyncio.wait_for(other.read(1), 2), b"")
        self.assertTrue(self.upstreams.empty())
        first.write_eof()
        await asyncio.wait_for(self.app.wait_idle(), 3)
        self.assertFalse(phone.is_closing())  # test peer did not cooperate
        _, w = await self.connect()
        await self.proxy_side()
        w.close()
        self.app.publish()
        self.assertEqual(json.loads(self.status.read_bytes())["sessions_rejected"], 1)

    async def test_idle_duplex_session_has_no_half_close_deadline(self):
        _, windows = await self.connect()
        up, _ = await self.proxy_side()
        await asyncio.sleep(.65)
        windows.write(b"still connected")
        self.assertEqual(await asyncio.wait_for(up.readexactly(15), 2), b"still connected")

    async def test_slow_receiver_does_not_block_other_direction(self):
        down, windows = await self.connect()
        up, phone = await self.proxy_side()
        block = bytes(range(256)) * 256

        async def large_send():
            for _ in range(128):
                windows.write(block)
                await windows.drain()

        sending = asyncio.create_task(large_send())
        try:
            await asyncio.sleep(.10)
            phone.write(b"concurrent control")
            self.assertEqual(await asyncio.wait_for(down.readexactly(18), 3), b"concurrent control")
            async with asyncio.timeout(12):
                for _ in range(128):
                    self.assertEqual(await up.readexactly(len(block)), block)
                await sending
        finally:
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)

    async def test_disconnected_upstream_starts_next_pair_with_no_replayed_bytes(self):
        down, windows = await self.connect()
        old_reader, old_phone = await self.proxy_side()
        windows.write(b"old")
        self.assertEqual(await asyncio.wait_for(old_reader.readexactly(3), 2), b"old")
        old_phone.transport.abort()
        try:
            self.assertEqual(await asyncio.wait_for(down.read(), 3), b"")
        except ConnectionResetError:
            pass
        windows.close()
        await asyncio.wait_for(self.app.wait_idle(), 3)
        _, fresh = await self.connect()
        new_reader, _ = await self.proxy_side()
        fresh.write(b"new")
        self.assertEqual(await asyncio.wait_for(new_reader.readexactly(3), 2), b"new")

    async def test_upstream_offline_closes_windows_without_inserting_data(self):
        self.up.close()
        await self.up.wait_closed()
        down, _ = await self.connect()
        self.assertEqual(await asyncio.wait_for(down.read(), self.relay_module.CONNECT_TIMEOUT + 2), b"")
        await self.app.wait_idle()
        self.app.publish()
        self.assertEqual(json.loads(self.status.read_bytes())["last_result"], "upstream_unavailable")

    async def test_unexpected_loopback_listener_never_receives_application_bytes(self):
        self.app.check_upstream = lambda: False
        r, w = await self.connect()
        w.write(b"test enrollment record")
        try:
            received = await asyncio.wait_for(r.read(), 3)
        except ConnectionResetError:
            received = b""
        self.assertEqual(received, b"")
        await self.app.wait_idle()
        self.assertTrue(self.upstreams.empty())
        self.assertEqual(self.app.last_result, "upstream_unavailable")


class SettingsTests(unittest.TestCase):
    def test_rejects_wildcard_loopback_peer_and_injected_interface(self):
        self.assertIsNotNone(importlib.util.find_spec("relay"), "Pi relay is missing")
        from relay import Settings
        default = dict(interface="usb99", listen_address="192.0.2.1",
                       windows_address="192.0.2.2", listen_port=43123, upstream_port=43124)
        self.assertEqual(Settings.parse(default).interface, "usb99")
        for key, value in (("listen_address", "0.0.0.0"), ("windows_address", "127.0.0.1"),
                           ("windows_address", "192.0.2.1"), ("interface", "../eth0"),
                           ("listen_port", 80), ("listen_port", True), ("upstream_port", 43123)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                Settings.parse({**default, key: value})

    def test_gadget_proof_requires_kernel_device_type_and_carrier(self):
        self.assertIsNotNone(importlib.util.find_spec("gadget"), "missing kernel checks")
        from gadget import gadget_up
        with tempfile.TemporaryDirectory() as d:
            iface = Path(d) / "eth0"
            iface.mkdir()
            (iface / "carrier").write_text("1\n")
            (iface / "uevent").write_text("DEVTYPE=wlan\n")
            self.assertFalse(gadget_up(iface))
            (iface / "uevent").write_text("INTERFACE=eth0\nDEVTYPE=gadget\n")
            self.assertTrue(gadget_up(iface))
            (iface / "carrier").write_text("0\n")
            self.assertFalse(gadget_up(iface))

    def test_listener_identity_rejects_another_local_user_or_wildcard(self):
        from gadget import owned_loopback_listener
        row = "0: 0100007F:A874 00000000:0000 0A 00000000:00000000 00:00000000 00000000 61200 0 100"
        self.assertTrue(owned_loopback_listener([row], 43124, 61200))
        self.assertFalse(owned_loopback_listener([row], 43124, 61199))
        self.assertFalse(owned_loopback_listener([row.replace("0100007F", "00000000")], 43124, 61200))
        self.assertFalse(owned_loopback_listener([row.replace(" 0A ", " 01 ")], 43124, 61200))
