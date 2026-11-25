import asyncio
import plistlib
import socket
import struct
import sys

import pytest

from usblinklab import transport


HEADER = struct.Struct("<IIII")


def device_record(handle, serial, connection="USB"):
    return {
        "DeviceID": handle,
        "Properties": {
            "DeviceID": handle,
            "SerialNumber": serial,
            "ConnectionType": connection,
        },
    }


def response(tag, payload):
    body = plistlib.dumps(payload)
    return HEADER.pack(HEADER.size + len(body), 1, 8, tag) + body


async def with_mux(monkeypatch, handler, test):
    handlers = set()

    async def handle(reader, writer):
        handlers.add(asyncio.current_task())
        try:
            length, version, kind, tag = HEADER.unpack(await reader.readexactly(HEADER.size))
            assert (version, kind) == (1, 8)
            request = plistlib.loads(await reader.readexactly(length - HEADER.size))
            await handler(request, tag, reader, writer)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]

        async def open_test_service():
            return await asyncio.open_connection("127.0.0.1", port)

        monkeypatch.setattr(transport, "_open_service", open_test_service)
        await test()
        await asyncio.gather(*handlers)


def test_usb_selected_by_stable_id_and_relisted_on_the_connect_stream(monkeypatch):
    seen = []
    serial = "00008110-007328DC11E0A01E"
    other = "00008110-007328DC11E0A01F"

    async def handler(request, tag, reader, writer):
        assert request["MessageType"] == "ListDevices"
        seen.append(request["MessageType"])
        selected_handle = 3 if len(seen) == 1 else 7
        packet = response(tag, {"DeviceList": [
            device_record(8, serial, "Network"),
            device_record(9, other),
            device_record(selected_handle, serial),
        ]})
        for index in range(0, len(packet), 13):
            writer.write(packet[index:index + 13])
            await writer.drain()
            await asyncio.sleep(0)
        if len(seen) == 2:
            length, version, kind, connect_tag = HEADER.unpack(
                await reader.readexactly(HEADER.size)
            )
            assert (version, kind) == (1, 8)
            assert connect_tag != tag
            connect = plistlib.loads(await reader.readexactly(length - HEADER.size))
            seen.append(connect["MessageType"])
            assert connect["DeviceID"] == 7
            assert connect["PortNumber"] == socket.htons(43123)
            writer.write(response(connect_tag, {"MessageType": "Result", "Number": 0})
                         + b"ULAB")
            await writer.drain()
            assert await reader.readexactly(4) == b"pong"

    async def test():
        devices = await transport.discover()
        assert len(devices) == 2
        assert serial not in repr(devices)
        selected = next(device for device in devices if device.stable_id ==
                        transport._stable_id(serial))
        reader, writer = await transport.connect(selected)
        try:
            assert await reader.readexactly(4) == b"ULAB"
            writer.write(b"pong")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    asyncio.run(with_mux(monkeypatch, handler, test))
    assert seen == ["ListDevices", "ListDevices", "Connect"]


def test_network_device_is_not_offered_or_connected(monkeypatch):
    async def handler(request, tag, reader, writer):
        assert request["MessageType"] == "ListDevices"
        network = device_record(4, "TEST-NETWORK", "Network")
        writer.write(response(tag, {"DeviceList": [network]}))
        await writer.drain()
        assert await reader.read() == b""

    async def test():
        assert await transport.discover() == []
        selected = transport.Device(transport._stable_id("TEST-NETWORK"), "Test")
        with pytest.raises(transport.TransportError, match="device_gone") as error:
            await transport.connect(selected)
        assert error.value.code == "device_gone"

    asyncio.run(with_mux(monkeypatch, handler, test))


@pytest.mark.parametrize("payload", [
    {"DeviceList": [{"Properties": {"ConnectionType": "USB"}}]},
    {"DeviceList": {"1": {}}},
    {"DeviceList": [device_record(1, "TEST-1"), device_record(1, "TEST-2", "Network")]},
    {"DeviceList": [device_record(1, "TEST-1"), device_record(2, "test-1")]},
    {"DeviceList": [device_record(True, "TEST-1")]},
])
def test_invalid_usb_records_fail_closed(monkeypatch, payload):
    async def handler(request, tag, reader, writer):
        writer.write(response(tag, payload))
        await writer.drain()

    async def test():
        with pytest.raises(transport.TransportError, match="mux_protocol"):
            await transport.discover()

    asyncio.run(with_mux(monkeypatch, handler, test))


@pytest.mark.parametrize("packet", [
    lambda tag: HEADER.pack(2**28, 1, 8, tag),
    lambda tag: HEADER.pack(30, 1, 8, tag),
    lambda tag: response(tag + 1, {"DeviceList": []}),
    lambda tag: HEADER.pack(HEADER.size + 7, 1, 8, tag) + b"garbage",
    lambda tag: (HEADER.pack(HEADER.size + 63, 1, 8, tag) +
                 b'<!DOCTYPE plist [<!ENTITY example "123">]><plist/>'.ljust(63)),
])
def test_bad_mux_envelope_is_rejected(monkeypatch, packet):
    async def handler(request, tag, reader, writer):
        writer.write(packet(tag))
        await writer.drain()

    async def test():
        with pytest.raises(transport.TransportError, match="mux_protocol"):
            await transport.discover()

    asyncio.run(with_mux(monkeypatch, handler, test))


@pytest.mark.parametrize(("number", "code"), [(2, "device_gone"), (3, "listener_refused"),
                                              (7, "connect_rejected"), (False, "mux_protocol")])
def test_mux_connect_result_is_sanitized(monkeypatch, number, code):
    async def handler(request, tag, reader, writer):
        writer.write(response(tag, {"DeviceList": [device_record(1, "TEST-1")]}))
        await writer.drain()
        length, _, _, tag = HEADER.unpack(await reader.readexactly(HEADER.size))
        await reader.readexactly(length - HEADER.size)
        writer.write(response(tag, {"MessageType": "Result", "Number": number,
                                    "String": "identifying text"}))
        await writer.drain()
        assert await reader.read() == b""

    async def test():
        selected = transport.Device(transport._stable_id("TEST-1"), "Test")
        with pytest.raises(transport.TransportError, match=code) as error:
            await transport.connect(selected)
        assert str(error.value) == code

    asyncio.run(with_mux(monkeypatch, handler, test))


def test_cancel_during_mux_exchange_closes_client_socket(monkeypatch):
    request_read = asyncio.Event()

    async def handler(request, tag, reader, writer):
        request_read.set()
        assert await asyncio.wait_for(reader.read(), timeout=2) == b""

    async def test():
        task = asyncio.create_task(transport.discover())
        await asyncio.wait_for(request_read.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(with_mux(monkeypatch, handler, test))


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows TCP owner table")
def test_tcp_owner_checks_actual_local_socket():
    from usblinklab import windows_usb
    import os

    async def test():
        connected = asyncio.Future()

        def accepted(reader, writer):
            connected.set_result(writer)

        server = await asyncio.start_server(accepted, "127.0.0.1", 0)
        async with server:
            _, client = await asyncio.open_connection(*server.sockets[0].getsockname())
            listener = await connected
            try:
                assert await asyncio.to_thread(
                    windows_usb.connected_owner,
                    client.get_extra_info("peername"), client.get_extra_info("sockname")
                ) == os.getpid()
            finally:
                client.close()
                listener.close()
                await client.wait_closed()
                await listener.wait_closed()

    asyncio.run(test())


def test_unverified_daemon_receives_no_mux_request(monkeypatch):
    observed = []

    async def test():
        handled = asyncio.Event()

        async def accepted(reader, writer):
            try:
                observed.append(await asyncio.wait_for(reader.read(), timeout=2))
            finally:
                writer.close()
                await writer.wait_closed()
                handled.set()

        server = await asyncio.start_server(accepted, "127.0.0.1", 0)
        async with server:
            monkeypatch.setattr(transport, "_DAEMON", server.sockets[0].getsockname())
            monkeypatch.setattr(transport.windows_usb, "service_pid", lambda: 100)
            monkeypatch.setattr(transport.windows_usb, "connected_owner", lambda local, remote: 101)
            with pytest.raises(transport.TransportError, match="mux_owner_mismatch"):
                await transport.discover()
            await asyncio.wait_for(handled.wait(), timeout=2)

    asyncio.run(test())
    assert observed == [b""]


def test_mux_timeout_closes_its_socket(monkeypatch):
    async def handler(request, tag, reader, writer):
        assert await asyncio.wait_for(reader.read(), timeout=2) == b""

    async def test():
        monkeypatch.setattr(transport, "_TIMEOUT", 0.1)
        with pytest.raises(transport.TransportError, match="mux_timeout"):
            await transport.discover()

    asyncio.run(with_mux(monkeypatch, handler, test))
