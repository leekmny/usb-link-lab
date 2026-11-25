"""Production path selection, routing and legacy identity regressions."""
import asyncio
import secrets
import sys
from dataclasses import replace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit, QPushButton, QSpinBox

import launch
from usblinklab import pairing, transport
from usblinklab.window import Window


def module():
    from usblinklab import pi_transport
    return pi_transport


ROW = dict(index=19, route_index=19, source_index=19, source="10.7.9.6", length=28,
           prefix="10.7.9.0/28", next_hop="0.0.0.0", preferred=True, skip=False,
           pnp=r"USB\VID_2E8A&PID_0013\LAB", physical=True, up=True,
           guid="e3cc0c83-44d7-4e48-9380-127634a273af", alias="Lab interface")


def test_chooses_verified_peer_on_connected_subnet():
    link = module().verified_link(ROW, "10.7.9.1")
    assert (link.index, link.source, link.alias) == (19, "10.7.9.6", "Lab interface")


@pytest.mark.parametrize("changes", [
    {"pnp": r"PCI\VEN_1234"}, {"pnp": r"USB\VID_1111&PID_2222\LAB"},
    {"up": False}, {"physical": False}, {"source_index": 7}, {"route_index": 7},
    {"source": "10.7.9.1"}, {"source": "10.5.8.4"}, {"prefix": "0.0.0.0/0"},
    {"next_hop": "10.7.9.3"}, {"preferred": False}, {"skip": True},
])
def test_refuses_wifi_gateway_or_disconnected_even_when_label_looks_right(changes):
    with pytest.raises(transport.TransportError):
        module().verified_link(ROW | changes, "10.7.9.1")


@pytest.mark.parametrize("destination", ["127.0.0.1", "localhost", "224.0.0.1", "10.7.9.15", "10.7.9.0"])
def test_rejects_loopback_multicast_or_subnet_boundaries(destination):
    with pytest.raises(transport.TransportError):
        module().verified_link(ROW, destination)


def test_switching_address_cannot_rebind_existing_jobs_or_replace_direct_secret(tmp_path):
    store = pairing.PairingStore(tmp_path / "pairing.dpapi")
    direct = pairing.Pairing("a" * 64, secrets.token_bytes(32))
    store.save(direct)
    before = store.path.read_bytes()
    opt = module().PathSettings("relay", "10.7.9.1", 43123, False)
    relay = module().PiRelay(opt, store)
    assert relay.peer_id() != direct.device
    enrolled = pairing.Pairing(relay.peer_id(), secrets.token_bytes(32))
    store.save(enrolled)
    assert store.path.read_bytes() == before
    assert store.load(direct.device) == direct
    assert store.load(enrolled.device) == enrolled
    assert direct.binding != enrolled.binding
    moved = module().PiRelay(replace(opt, destination="10.2.3.4"), store)
    assert moved.peer_id() == enrolled.device
    reuse = module().PiRelay(replace(opt, reuse_direct=True), store)
    assert reuse.peer_id() == direct.device


def test_link_selection_rechecked_before_socket_open(tmp_path, monkeypatch):
    pi = module()
    current = pi.verified_link(ROW, "10.7.9.1")
    relay = pi.PiRelay(pi.PathSettings("relay", "10.7.9.1", 43123, False),
                       pairing.PairingStore(tmp_path / "pairing.dpapi"))
    monkeypatch.setattr(pi, "inspect_link", lambda dest: current)
    device, = asyncio.run(relay.discover())
    current = replace(current, guid="e8f6fbbb-3fe6-46b1-9d8b-f217003042ae")
    with pytest.raises(transport.TransportError, match="pi_link_changed"):
        asyncio.run(relay.connect(device))


def test_normal_window_can_select_persist_and_reload_relay(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    pi = module()
    monkeypatch.setattr(pi, "inspect_link", lambda dest: pi.verified_link(ROW, dest))
    ctl = launch.production_controller(tmp_path)
    window = Window(ctl, tmp_path)
    window.show()
    commands = []
    ctl.changed.connect(lambda topic, value: commands.append((topic, value)))
    # The production settings UI must drive the worker, with no direct iPhone required.
    async def no_phones():
        return []
    monkeypatch.setattr(transport, "discover", no_phones)
    ctl.start()
    def wait(predicate):
        from time import monotonic, sleep
        deadline = monotonic() + 5
        while not predicate() and monotonic() < deadline:
            qt.processEvents()
            sleep(0.01)
        assert predicate()
    try:
        wait(lambda: ("connection", "no_device") in commands)
        def edit():
            dialog = window.findChild(QDialog)
            selector = dialog.findChild(QComboBox, "connectionPath")
            selector.setCurrentIndex(selector.findData("relay"))
            dialog.findChild(QLineEdit, "relayAddress").setText("10.7.9.1")
            dialog.findChild(QSpinBox, "relayPort").setValue(43201)
            dialog.findChild(QPushButton, "primary").click()
        QTimer.singleShot(0, edit)
        window._settings_dialog()
        wait(lambda: ("connection", "select_device") in commands)
        assert window.devices.count() == 2
        assert "10.7.9.1" in window.diagnostic_text.text()
        assert "43201" in window.diagnostic_text.text()
        assert window.tr_key("pi_route_ready") == window.connection_status.text()
        assert window.device_title.fontMetrics().horizontalAdvance(window.device_title.text()) <= window.device_title.width()
        fresh = launch.production_controller(tmp_path)
        try:
            assert isinstance(fresh.usb, pi.PiRelay)
            assert fresh.path_settings.port == 43201
            assert fresh.path_settings.destination == "10.7.9.1"
        finally:
            fresh.shutdown()
    finally:
        ctl.shutdown()
        window.close()


def test_invalid_saved_path_cannot_fall_back_to_apple_service(tmp_path):
    (tmp_path / "transport.json").write_text('{"mode":"unexpected"}')
    ctl = launch.production_controller(tmp_path)
    try:
        with pytest.raises(transport.TransportError, match="pi_config_invalid"):
            asyncio.run(ctl.usb.discover())
    finally:
        ctl.shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native IPv4 socket options")
@pytest.mark.parametrize("changed", [False, True])
def test_pinned_socket_rechecks_actual_route_before_returning_stream(changed, monkeypatch):
    pi = module()
    async def exercise():
        peer_data = asyncio.get_running_loop().create_future()
        async def peer(reader, writer):
            data = await reader.read(8)
            peer_data.set_result(data)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        async with server:
            index = pi.best_index("127.0.0.1")
            link = pi.GadgetLink("loopback-test", index, "127.0.0.1", "synthetic")
            port = server.sockets[0].getsockname()[1]
            if changed:
                monkeypatch.setattr(pi, "best_index", lambda _: index + 1)
                with pytest.raises(transport.TransportError, match="pi_link_changed"):
                    await pi.open_gadget(link, "127.0.0.1", port)
                assert await asyncio.wait_for(peer_data, 2) == b""
            else:
                _, writer = await pi.open_gadget(link, "127.0.0.1", port)
                writer.write(b"local")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                assert await asyncio.wait_for(peer_data, 2) == b"local"
    asyncio.run(exercise())


def test_saved_direct_queue_released_only_after_authenticated_relay_peer(tmp_path, monkeypatch):
    # Swap only the OS link boundaries for a real local peer running the ULAB server.
    from test_controller import SyntheticUSB, wait_for
    from usblinklab.protocol import Mode

    qt = QApplication.instance() or QApplication([])
    pi = module()
    root = tmp_path / "client"
    settings = pi.PathSettings("relay", "10.7.9.1", 43123, True)
    settings.save(root)
    secret = secrets.token_bytes(32)
    original = pairing.Pairing("b" * 64, secret)
    vault = pairing.PairingStore(root / "pairing.dpapi")
    vault.save(original)
    original_record = vault.path.read_bytes()
    peer = SyntheticUSB(tmp_path / "peer", secrets.token_bytes(32))
    monkeypatch.setattr(pi, "inspect_link", lambda dest: pi.verified_link(ROW, dest))
    async def open_synthetic(link, dest, port):
        return await peer.connect(peer.device)
    monkeypatch.setattr(pi, "open_gadget", open_synthetic)
    ctl = launch.production_controller(root)
    events = []
    ctl.changed.connect(lambda topic, value: events.append((topic, value)))
    ctl.start()
    try:
        wait_for(lambda: ("connection", "select_device") in events)
        ctl.submit("new_pair_qr", original.device, "no-overwrite")
        wait_for(lambda: any(topic == "pairing_request_error" and value["reason"] == "pi_reuse_only"
                             for topic, value in events))
        ctl.submit("enqueue", original.device, Mode.AES256GCM, "queued for original iPhone", ())
        wait_for(lambda: ("enqueue_result", True) in events)
        ctl.submit("connect", original.device)
        wait_for(lambda: peer.attempts == 1 and ctl.connection.done())
        assert ("connection", "connected") not in events
        assert peer.storage.received() == []
        assert vault.path.read_bytes() == original_record
        peer.secret = secret
        ctl.submit("connect", original.device)
        wait_for(lambda: any(topic == "item" and value["status"] == "complete" for topic, value in events))
        assert ("connection", "connected") in events
        assert [item.text for item in peer.storage.received()] == ["queued for original iPhone"]
    finally:
        ctl.shutdown()
        qt.processEvents()
