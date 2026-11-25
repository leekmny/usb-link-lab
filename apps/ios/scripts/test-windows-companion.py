"""Exercise the Windows companion against the Swift test server on IPv4 loopback."""

from __future__ import annotations

import asyncio
from collections import Counter
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys


from contextlib import asynccontextmanager

@asynccontextmanager
async def companion_connection(root):
    if os.environ.get("ULAB_TEST_RELAY") == "1":
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
        from companion_relay import relay_connection
        async with relay_connection(root) as pair:
            yield pair
    else:
        reader, writer = await asyncio.open_connection("127.0.0.1", 43123)
        try:
            yield reader, writer
        finally:
            writer.close()
            await writer.wait_closed()


async def exchange(root, psk, mode_type, session_type, storage_type):
    fixtures = json.loads((root / "fixtures.json").read_bytes())
    outgoing = fixtures["fromPython"]
    incoming = fixtures["fromSwift"]
    store_root = root / "windows-store"
    storage = storage_type(store_root)
    jobs = []
    for sample in outgoing:
        source = root / sample["file"]
        mode = mode_type(sample["mode"])
        if sample["kind"] == "text":
            jobs.append(storage.stage_text(source.read_bytes().decode("utf-8"), mode))
        else:
            jobs.append(storage.stage_file(
                source, mode, kind=sample["kind"], name=sample["name"],
                media_type=sample["mediaType"],
            ))

    completed = []
    received_all = asyncio.Event()

    def update(event):
        if event.status == "complete":
            completed.append(event)
            if sum(item.direction == "incoming" for item in completed) >= len(incoming):
                received_all.set()

    async with companion_connection(root) as (reader, writer):
        session = session_type(reader, writer, psk, storage, update, client=True)

        async def send_and_drain():
            await session.wait_ready()
            for job in jobs:
                await session.send(job)
            await received_all.wait()
            await session.request_drain()

        runner = asyncio.create_task(session.run())
        transfers = asyncio.create_task(send_and_drain())
        try:
            done, _ = await asyncio.wait({runner, transfers}, return_when=asyncio.FIRST_COMPLETED)
            if runner in done:
                runner.result()
            await transfers
            outcome = await runner
            if outcome != "drained":
                raise RuntimeError(f"Expected a coordinated drain, got {outcome!r}")
        finally:
            for task in (transfers, runner):
                if not task.done():
                    task.cancel()
            await asyncio.gather(transfers, runner, return_exceptions=True)


    expected_events = Counter({
        ("outgoing", item["mode"], item["kind"]): 1 for item in outgoing
    }) + Counter({
        ("incoming", item["mode"], item["kind"]): 1 for item in incoming
    })
    observed_events = Counter((event.direction, int(event.mode), event.kind) for event in completed)
    if observed_events != expected_events:
        raise AssertionError(f"Unexpected completion events: {observed_events}")

    saved = storage_type(store_root).received()
    if len(saved) != len(incoming):
        raise AssertionError(f"Expected {len(incoming)} persisted items, found {len(saved)}")
    expected_by_kind = {(item["mode"], item["kind"]): item for item in incoming}
    for item in saved:
        sample = expected_by_kind.pop((int(item.mode), item.kind))
        contents = (root / sample["file"]).read_bytes()
        if (item.name, item.media_type, item.size) != (sample["name"], sample["mediaType"], len(contents)):
            raise AssertionError(f"Incorrect persisted metadata for mode {item.mode}/{item.kind}")
        if item.sha256 != hashlib.sha256(contents).hexdigest() or item.path.read_bytes() != contents:
            raise AssertionError(f"Incorrect persisted bytes for mode {item.mode}/{item.kind}")
        if item.kind == "text" and item.text != contents.decode("utf-8"):
            raise AssertionError(f"Incorrect persisted text for mode {item.mode}")

    print(
        f"Windows companion verified {len(outgoing)} outgoing ACKs and {len(saved)} persisted incoming items "
        "over loopback across all four modes and a coordinated drain.",
        flush=True,
    )



def load_mac_bridge(path):
    """Adapt only the Windows native loader for the host's identical bridge."""
    library = ctypes.CDLL(path)
    ptr = ctypes.c_void_p
    size = ctypes.c_size_t
    declarations = (
        ("ulab_pake_new", [ctypes.c_int, ptr, size], ptr),
        ("ulab_pake_write", [ptr, ctypes.c_int, ptr, size, ctypes.POINTER(size)], ctypes.c_int),
        ("ulab_pake_read", [ptr, ctypes.c_int, ptr, size], ctypes.c_int),
        ("ulab_pake_derive", [ptr, ptr], ctypes.c_int),
        ("ulab_pake_free", [ptr], None),
    )
    for name, arguments, result in declarations:
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = result
    return library


async def enroll(root, method, psk, mode_type, session_type, storage_type):
    from usblinklab import pin_pairing

    native = os.environ.get("ULAB_NATIVE_TEST_LIBRARY")
    if method == "pin" and native:
        bridge = load_mac_bridge(native)
        pin_pairing._native = lambda: bridge

    storage = storage_type(root / "windows-enrollment-store")
    for initial in (True, False):
        async with companion_connection(root) as (reader, writer):
            if initial and method == "pin":
                psk = await pin_pairing.pair_client(reader, writer, os.environ.pop("ULAB_TEST_PIN"))
            session = session_type(reader, writer, psk, storage, lambda _: None, client=True)
            job = storage.stage_text("USBLinkLab synthetic enrollment test", mode_type.AES256GCM)
            run = asyncio.create_task(session.run())
            try:
                await session.wait_ready()
                await session.send(job)
                await session.request_drain()
                assert await run == "drained"
            finally:
                if not run.done():
                    run.cancel()
                await asyncio.gather(run, return_exceptions=True)
    print("Windows companion verified authenticated enrollment and saved-key reconnect.", flush=True)


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    source = Path(os.environ["ULAB_WINDOWS_SOURCE"]).resolve(strict=True)
    if not (source / "usblinklab" / "session.py").is_file():
        raise RuntimeError("ULAB_WINDOWS_SOURCE must identify the Windows companion apps/windows directory")
    method = os.environ.get("ULAB_TEST_ENROLLMENT")
    psk = bytes.fromhex(os.environ.pop("ULAB_TEST_PSK", ""))
    if method != "pin" and len(psk) != 32:
        raise RuntimeError("The synthetic pairing secret must contain 32 bytes")
    sys.path.insert(0, str(source))
    from usblinklab.protocol import Mode
    from usblinklab.session import Session
    from usblinklab.storage import Storage

    if method in ("qr", "pin"):
        task = enroll(root, method, psk, Mode, Session, Storage)
    else:
        task = exchange(root, psk, Mode, Session, Storage)
    asyncio.run(asyncio.wait_for(task, timeout=30))


if __name__ == "__main__":
    main()
