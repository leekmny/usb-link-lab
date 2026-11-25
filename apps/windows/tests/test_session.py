import asyncio
from pathlib import Path

import pytest


async def connected_pair(storage_a, storage_b, callback_a, callback_b):
    from usblinklab.session import Session

    key = bytes(range(32))
    accepted = asyncio.get_running_loop().create_future()

    def accept(reader, writer):
        accepted.set_result(Session(reader, writer, key, storage_b, callback_b, client=False))

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection(*listener.sockets[0].getsockname())
        a = Session(reader, writer, key, storage_a, callback_a)
        b = await accepted
    finally:
        listener.close()

    return a, b


@pytest.mark.parametrize("left_mode,right_mode", [(0, 1), (2, 3)])
def test_duplex_saves_original_bytes_and_persistent_metadata(tmp_path, left_mode, right_mode):
    from usblinklab.protocol import Mode
    from usblinklab.storage import Storage

    async def scenario():
        a_store, b_store = Storage(tmp_path / "a"), Storage(tmp_path / "b")
        a_events, b_events = [], []
        a, b = await connected_pair(a_store, b_store, a_events.append, b_events.append)
        a_run, b_run = asyncio.create_task(a.run()), asyncio.create_task(b.run())
        text = "a" * 65535 + "\uC548\uB155 USB"
        source = tmp_path / "selected.bin"
        content = bytes(range(256)) * 256 + b"\x00"
        source.write_bytes(content)
        a_job = a_store.stage_text(text, Mode(left_mode))
        b_job = b_store.stage_file(source, Mode(right_mode))
        try:
            await asyncio.gather(a.wait_ready(), b.wait_ready())
            await asyncio.gather(a.send(a_job), b.send(b_job))

            left_item, = Storage(tmp_path / "a").received()
            right_item, = Storage(tmp_path / "b").received()
            assert (left_item.mode, right_item.mode) == (Mode(right_mode), Mode(left_mode))
            assert left_item.path.read_bytes() == content
            assert right_item.text == text
            assert right_item.path.read_bytes() == text.encode("utf-8")
            for events in (a_events, b_events):
                assert {e.direction for e in events if e.status == "complete"} == {"incoming", "outgoing"}
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(a_run, b_run, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_enqueued_empty_file_survives_coordinated_drain(tmp_path: Path):
    from usblinklab.protocol import Mode
    from usblinklab.session import DrainRequired
    from usblinklab.storage import Storage

    async def scenario():
        a_store, b_store = Storage(tmp_path / "a"), Storage(tmp_path / "b")
        source = tmp_path / "empty"
        source.touch()
        job = a_store.stage_file(source, Mode.HMACSHA256)
        a, b = await connected_pair(a_store, b_store, lambda _: None, lambda _: None)
        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        try:
            await asyncio.gather(a.wait_ready(), b.wait_ready())
            await asyncio.gather(a.request_drain(), b.request_drain())
            assert await asyncio.gather(*runs) == ["drained", "drained"]
            with pytest.raises(DrainRequired):
                await a.send(job)
            assert job.path.exists()
            assert b_store.received() == []
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, return_exceptions=True)

        a, b = await connected_pair(a_store, b_store, lambda _: None, lambda _: None)
        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        try:
            await a.wait_ready()
            await a.send(job)
            item, = b_store.received()
            assert item.size == 0
            assert item.path.read_bytes() == b""
            assert item.mode is Mode.HMACSHA256
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))

@pytest.mark.parametrize("mode,replace_hash,committed", [(0, False, False), (0, True, True), (1, True, False), (2, True, False), (3, True, False)])
def test_plaintext_replacement_with_recomputed_hash(tmp_path, mode, replace_hash, committed):
    import hashlib

    from usblinklab.protocol import (
        AuthenticationError, Frame, FrameType, Mode, ProtocolError,
        encode_body, encode_frame, handshake, read_frame,
    )
    from usblinklab.session import Session
    from usblinklab.storage import Storage

    async def scenario():
        key = bytes(range(32))
        storage = Storage(tmp_path / "receiver")
        accepted = asyncio.get_running_loop().create_future()

        def accept(reader, writer):
            receiver = Session(reader, writer, key, storage, lambda _: None, client=False)
            accepted.set_result(asyncio.create_task(receiver.run()))

        listener = await asyncio.start_server(accept, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(*listener.sockets[0].getsockname())
        listener.close()
        receiver_task = await accepted
        try:
            out_keys, in_keys = await handshake(reader, writer, key, client=True)
            item_id, mode_value = bytes(range(1, 17)), Mode(mode)

            def encoded(type_, sequence, index, body):
                if isinstance(body, dict):
                    body = encode_body(body)
                return encode_frame(Frame(type_, mode_value, sequence, item_id, index, body), out_keys)

            writer.write(encoded(FrameType.OPEN, 0, 0, {"mode": mode}))
            await writer.drain()
            assert (await read_frame(reader, in_keys, 0)).type is FrameType.ACK

            original, changed = b"original", b"replaced"
            assert len(original) == len(changed)
            writer.write(encoded(FrameType.META, 1, 0, {
                "kind": "file", "name": "test.bin", "media_type": "application/octet-stream",
                "size": len(original), "chunk_size": 65536,
            }))
            data = bytearray(encoded(FrameType.DATA, 2, 0, original))
            if mode_value in (Mode.PLAINTEXT, Mode.HMACSHA256):
                data[76:76 + len(original)] = changed
            else:
                data[76] ^= 1
            writer.write(data)
            if mode_value is Mode.PLAINTEXT:
                digest = hashlib.sha256(changed if replace_hash else original).hexdigest()
                writer.write(encoded(FrameType.END, 3, 1, {
                    "bytes": len(original), "chunks": 1, "sha256": digest,
                }))
            await writer.drain()

            if committed:
                final_ack = await read_frame(reader, in_keys, 1)
                assert final_ack.type is FrameType.ACK
                assert final_ack.body == b'{"stage":"complete"}'
                item, = storage.received()
                assert item.path.read_bytes() == changed
                assert item.mode is Mode.PLAINTEXT
            else:
                with pytest.raises(ProtocolError if mode == 0 else AuthenticationError):
                    await receiver_task
                assert storage.received() == []
                assert not list(storage.incoming.iterdir())
        finally:
            writer.close()
            await writer.wait_closed()
            await asyncio.gather(receiver_task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("fault", ["persistence", "lost_ack"])
def test_failed_storage_and_lost_complete_ack_report_unconfirmed(tmp_path, monkeypatch, fault):
    from usblinklab.protocol import FrameType, Mode
    from usblinklab.session import SessionError
    from usblinklab.storage import Storage

    async def scenario():
        a_store, b_store = Storage(tmp_path / "a"), Storage(tmp_path / "b")
        a_events, b_events = [], []
        a, b = await connected_pair(a_store, b_store, a_events.append, b_events.append)
        if fault == "persistence":
            original_sync = b_store._sync

            def fail_record_sync(stream):
                if str(stream.name).endswith("metadata.part"):
                    raise OSError("Simulated filesystem persistence error")
                original_sync(stream)

            monkeypatch.setattr(b_store, "_sync", fail_record_sync)
        else:
            original_write = b.writer.write

            def drop_complete_ack(record):
                if record[5] == FrameType.ACK and b'"stage":"complete"' in record[76:]:
                    b.writer.transport.abort()
                    raise ConnectionResetError("Simulated failed ACK delivery")
                original_write(record)

            monkeypatch.setattr(b.writer, "write", drop_complete_ack)

        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        try:
            job = a_store.stage_text("committed message", Mode.AES256GCM)
            await a.wait_ready()
            with pytest.raises(SessionError):
                await a.send(job)
            await asyncio.gather(*runs, return_exceptions=True)
            assert a_events[-1].status == "unconfirmed"
            assert not list(b_store.incoming.iterdir())
            if fault == "persistence":
                assert b_events[-1].status == "interrupted"
                assert b_store.received() == []
                assert not list(b_store.complete.iterdir())
            else:
                assert b_events[-1].status == "complete"
                saved, = b_store.received()
                assert saved.text == "committed message"
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("action", ["drain", "cancel"])
def test_both_directions_are_active_when_session_drains_or_cancels(tmp_path, action):
    from usblinklab.protocol import Mode
    from usblinklab.session import SessionError
    from usblinklab.storage import Storage

    async def scenario():
        a_store, b_store = Storage(tmp_path / "a"), Storage(tmp_path / "b")
        a_events, b_events = [], []
        a_receiving, b_receiving = asyncio.Event(), asyncio.Event()

        def callback(events, receiving):
            def record(event):
                events.append(event)
                if event.direction == "incoming" and event.status == "receiving":
                    receiving.set()
            return record

        a, b = await connected_pair(a_store, b_store, callback(a_events, a_receiving), callback(b_events, b_receiving))
        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        source = tmp_path / "selected.bin"
        content = b"\x00\x01\x02\x03" * 1024 * 1024
        source.write_bytes(content)
        jobs = [a_store.stage_file(source, Mode.HMACSHA256), b_store.stage_file(source, Mode.CHACHA20POLY1305)]
        sends = [asyncio.create_task(a.send(jobs[0])), asyncio.create_task(b.send(jobs[1]))]
        try:
            await asyncio.gather(a_receiving.wait(), b_receiving.wait())
            if action == "drain":
                await asyncio.gather(a.request_drain(), b.request_drain())
                await asyncio.gather(*sends)
                assert await asyncio.gather(*runs) == ["drained", "drained"]
                for storage in (a_store, b_store):
                    received, = storage.received()
                    assert received.path.read_bytes() == content
            else:
                await a.close()
                assert all(isinstance(error, SessionError) for error in (
                    await asyncio.gather(*sends, return_exceptions=True)
                ))
                await asyncio.gather(*runs, return_exceptions=True)
                for events in (a_events, b_events):
                    assert {e.direction for e in events if e.status == "interrupted"} == {"incoming", "outgoing"}
                for storage in (a_store, b_store):
                    assert not list(storage.incoming.iterdir())
                    assert storage.received() == []
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, *sends, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("limit", ["ids", "byte_budget"])
def test_admission_preserves_next_snapshot_when_limits_require_drain(tmp_path, monkeypatch, limit):
    from usblinklab import session
    from usblinklab.protocol import Mode
    from usblinklab.session import DrainRequired
    from usblinklab.storage import Storage

    async def scenario():
        a_store, b_store = Storage(tmp_path / "a"), Storage(tmp_path / "b")
        next_job = a_store.stage_text("another mode", Mode.PLAINTEXT)
        a, b = await connected_pair(a_store, b_store, lambda _: None, lambda _: None)
        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        try:
            await a.wait_ready()
            if limit == "ids":
                a._local_ids.update(number.to_bytes(16, "big") for number in range(1, 4096))
                last_job = a_store.stage_text("the 4096th item", Mode.AES256GCM)
                await a.send(last_job)
                item, = b_store.received()
                assert item.text == "the 4096th item"
                assert item.mode is Mode.AES256GCM
            else:
                monkeypatch.setattr(session, "MAX_BODY_BYTES", (6 + 2 * 4096) * 4096)
            with pytest.raises(DrainRequired):
                await a.send(next_job)
            assert await asyncio.gather(*runs) == ["drained", "drained"]
            assert next_job.path.read_bytes() == b"another mode"
            assert next_job.mode is Mode.PLAINTEXT
            assert len(b_store.received()) == (1 if limit == "ids" else 0)
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))

def test_complete_ack_received_after_cancel_does_not_upgrade_status(tmp_path, monkeypatch):
    from usblinklab import session
    from usblinklab.protocol import FrameType, Mode
    from usblinklab.session import SessionError
    from usblinklab.storage import Storage

    async def scenario():
        a_store, b_store = Storage(tmp_path / "a"), Storage(tmp_path / "b")
        a_events = []
        a, b = await connected_pair(a_store, b_store, a_events.append, lambda _: None)
        ack_buffered, release = asyncio.Event(), asyncio.Event()
        original_read = session.read_frame

        async def read_with_delay(reader, keys, sequence):
            frame = await original_read(reader, keys, sequence)
            if reader is a.reader and frame.type is FrameType.ACK and frame.body == b'{"stage":"complete"}':
                ack_buffered.set()
                await release.wait()
            return frame

        monkeypatch.setattr(session, "read_frame", read_with_delay)
        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        job = a_store.stage_text("saved at the peer", Mode.AES256GCM)
        send = asyncio.create_task(a.send(job))
        try:
            await ack_buffered.wait()
            closing = asyncio.create_task(a.close())
            await asyncio.sleep(0)
            release.set()
            await closing
            with pytest.raises(SessionError):
                await send
            assert a_events[-1].status == "unconfirmed"
            received, = b_store.received()
            assert received.text == "saved at the peer"
        finally:
            release.set()
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, send, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))

def test_simultaneous_incoming_progress_does_not_extend_final_ack_deadline(tmp_path, monkeypatch):
    from usblinklab import session
    from usblinklab.protocol import (
        CHUNK_SIZE, Frame, FrameType, Mode, encode_body, encode_frame, handshake, read_frame,
    )
    from usblinklab.session import Session, SessionError
    from usblinklab.storage import Storage

    async def scenario():
        key = bytes(range(32))
        events = []
        storage = Storage(tmp_path / "receiver")
        accepted = asyncio.get_running_loop().create_future()
        monkeypatch.setattr(session, "ITEM_TIMEOUT", 0.65)

        def accept(reader, writer):
            accepted.set_result(Session(reader, writer, key, storage, events.append, client=False))

        listener = await asyncio.start_server(accept, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(*listener.sockets[0].getsockname())
        listener.close()
        receiver = await accepted
        run = asyncio.create_task(receiver.run())
        send = asyncio.create_task(receiver.send(storage.stage_text("awaiting peer", Mode.AES256GCM)))
        try:
            out_keys, in_keys = await handshake(reader, writer, key, client=True)
            mode = Mode.AES256GCM

            async def write(type_, sequence, item_id, index, body):
                if isinstance(body, dict):
                    body = encode_body(body)
                writer.write(encode_frame(Frame(type_, mode, sequence, item_id, index, body), out_keys))
                await writer.drain()

            opened = await read_frame(reader, in_keys, 0)
            assert opened.type is FrameType.OPEN
            await write(FrameType.ACK, 0, opened.item_id, 0, {"stage": "ready"})
            for sequence, type_ in enumerate((FrameType.META, FrameType.DATA, FrameType.END), 1):
                assert (await read_frame(reader, in_keys, sequence)).type is type_

            incoming_id = bytes(range(1, 17))
            await write(FrameType.OPEN, 1, incoming_id, 0, {"mode": int(mode)})
            assert (await read_frame(reader, in_keys, 4)).type is FrameType.ACK
            await write(FrameType.META, 2, incoming_id, 0, {
                "kind": "file", "name": "in-progress.bin", "media_type": "application/octet-stream",
                "size": 30 * CHUNK_SIZE, "chunk_size": CHUNK_SIZE,
            })

            async def feed_opposite_direction():
                for index in range(30):
                    await write(FrameType.DATA, 3 + index, incoming_id, index, bytes(CHUNK_SIZE))
                    await asyncio.sleep(0.07)

            feed = asyncio.create_task(feed_opposite_direction())
            try:
                with pytest.raises(SessionError, match="timed out"):
                    await run
                assert not feed.done()
                with pytest.raises(SessionError):
                    await send
                assert any(e.direction == "incoming" and e.transferred > 0 for e in events)
                assert {e.status for e in events if e.direction == "outgoing"} == {
                    "sending", "verifying", "unconfirmed",
                }
                assert storage.received() == []
                assert not list(storage.incoming.iterdir())
            finally:
                feed.cancel()
                await asyncio.gather(feed, return_exceptions=True)
        finally:
            writer.close()
            await writer.wait_closed()
            await receiver.close()
            await asyncio.gather(run, send, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))

@pytest.mark.parametrize("violation", [
    "wrong_id", "wrong_index", "wrong_mode", "duplicate_meta", "wrong_count",
    "missing_end", "duplicate_end", "reused_id", "open_after_drain",
])
def test_receiver_rejects_invalid_item_state(tmp_path, violation):
    import hashlib

    from usblinklab.protocol import (
        Frame, FrameType, Mode, ProtocolError, encode_body, encode_frame, handshake, read_frame,
    )
    from usblinklab.session import Session, SessionError
    from usblinklab.storage import Storage

    async def scenario():
        key = bytes(range(32))
        storage = Storage(tmp_path / "receiver")
        accepted = asyncio.get_running_loop().create_future()

        def accept(reader, writer):
            receiver = Session(reader, writer, key, storage, lambda _: None, client=False)
            accepted.set_result(asyncio.create_task(receiver.run()))

        listener = await asyncio.start_server(accept, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(*listener.sockets[0].getsockname())
        listener.close()
        receiver_task = await accepted
        try:
            out_keys, in_keys = await handshake(reader, writer, key, client=True)
            mode = Mode.HMACSHA256
            item_id, other_id = bytes(range(1, 17)), bytes(range(17, 33))
            sequence = 0

            async def write(type_, body, *, id_=item_id, mode_=mode, index=0):
                nonlocal sequence
                if isinstance(body, dict):
                    body = encode_body(body)
                writer.write(encode_frame(Frame(type_, mode_, sequence, id_, index, body), out_keys))
                sequence += 1
                await writer.drain()

            opening = {"mode": int(mode)}
            metadata = {
                "kind": "file", "name": "one-byte.bin", "media_type": "application/octet-stream",
                "size": 1, "chunk_size": 65536,
            }
            ending = {"bytes": 1, "chunks": 1, "sha256": hashlib.sha256(b"\xff").hexdigest()}

            if violation == "open_after_drain":
                await write(FrameType.DRAIN, {"stage": "request"}, id_=bytes(16), mode_=Mode.PLAINTEXT)
                assert (await read_frame(reader, in_keys, 0)).body == b'{"stage":"request"}'
                await write(FrameType.OPEN, opening)
            else:
                await write(FrameType.OPEN, opening)
                assert (await read_frame(reader, in_keys, 0)).body == b'{"stage":"ready"}'
                await write(FrameType.META, metadata)
                if violation == "wrong_id":
                    await write(FrameType.DATA, b"\xff", id_=other_id)
                elif violation == "wrong_index":
                    await write(FrameType.DATA, b"\xff", index=1)
                elif violation == "wrong_mode":
                    await write(FrameType.DATA, b"\xff", mode_=Mode.PLAINTEXT)
                elif violation == "duplicate_meta":
                    await write(FrameType.META, metadata)
                else:
                    await write(FrameType.DATA, b"\xff")
                    if violation == "missing_end":
                        writer.close()
                        await writer.wait_closed()
                    elif violation == "wrong_count":
                        await write(FrameType.END, ending | {"chunks": 0})
                    else:
                        await write(FrameType.END, ending, index=1)
                        assert (await read_frame(reader, in_keys, 1)).body == b'{"stage":"complete"}'
                        if violation == "duplicate_end":
                            await write(FrameType.END, ending, index=1)
                        else:
                            await write(FrameType.OPEN, opening)

            with pytest.raises(SessionError if violation == "missing_end" else ProtocolError):
                await receiver_task
            assert not list(storage.incoming.iterdir())
            saved = storage.received()
            if violation in ("duplicate_end", "reused_id"):
                [item] = saved
                assert item.path.read_bytes() == b"\xff"
            else:
                assert saved == []
        finally:
            writer.close()
            await writer.wait_closed()
            await asyncio.gather(receiver_task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))