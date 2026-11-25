"""Worker-thread boundary, local outbox, and USB connection supervision."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from functools import partial
import json
import os
import secrets
from pathlib import Path
import shutil
import tempfile
import threading
import time

from PySide6.QtCore import QObject, Signal

from . import transport
from .pairing import Pairing, PairingError, PairingStore, atomic_save, encode_pairing_qr
from .pin_pairing import CODE_TTL, PAIRING_TIMEOUT, PairingBackendError, PairingProtocolError, pair_client
from .protocol import AuthenticationError, Mode, ProtocolError
from .session import DrainRequired, Session, SessionCancelled, SessionError
from .storage import Job, Storage


MAX_JOBS = 32
RETRYABLE = {"interrupted", "unconfirmed"}


@dataclass
class OutboxEntry:
    job: Job
    binding: str
    status: str = "queued"

    def record(self):
        return {
            "id": self.job.id, "mode": int(self.job.mode), "kind": self.job.kind,
            "name": self.job.name, "media_type": self.job.media_type,
            "size": self.job.size, "path": str(self.job.path),
            "text": self.job.text[:480] if self.job.text and self.status == "complete" else self.job.text,
            "binding": self.binding, "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class PendingPairing:
    device: str
    request_id: str
    method: str
    credential: bytes | str = field(repr=False)
    expires: float


class Controller(QObject):
    changed = Signal(str, object)

    def __init__(self, root: Path, *, usb=transport):
        super().__init__()
        self.root = root
        self.usb = usb
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="USBLinkLab worker", daemon=True)
        self._main = None

    def start(self):
        self._main = self.loop.create_task(self._serve())
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main)
        finally:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    async def _serve(self):
        self.stop = asyncio.Event()
        self.wake = asyncio.Event()
        self.commands = set()
        self.connection = None
        self.session = None
        self.devices = {}
        self.refreshing = False
        self.items = OrderedDict()
        self.progress_signals = {}
        self.deleting_messages = set()
        self.enqueued = 0
        self.admitting = None
        self.disconnect_requested = False
        self.disconnect_epoch = 0
        self.pending_pairing = None
        self.pairing_timer = None
        self.pairings = PairingStore(self.root / "pairing.dpapi")
        self.outbox = self.root / "outbox.json"
        self.storage = None
        try:
            self.storage = Storage(self.root / "storage")
            self._restore()
        except (KeyError, TypeError, OSError, ValueError, RuntimeError):
            self.storage = None
            self.changed.emit("error", "storage_failed")
        self._start("discover", ())
        await self.stop.wait()
        self._clear_pending_pairing()
        if self.connection and not self.connection.done():
            await self._disconnect()
        await asyncio.gather(*self.commands, return_exceptions=True)

    def submit(self, name, *args):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._start, name, args)

    def shutdown(self):
        if self._main is None:
            self.loop.close()
            return
        self.submit("shutdown")
        self._thread.join()

    def _start(self, name, args):
        if name == "shutdown":
            self.stop.set()
            return
        if self.stop.is_set():
            return

        async def invoke():
            try:
                await getattr(self, "_" + name)(*args)
            except (OSError, ValueError, RuntimeError, ProtocolError):
                self.changed.emit("error", "storage_failed")
                if name == "enqueue":
                    self.changed.emit("enqueue_result", False)

        task = asyncio.create_task(invoke())
        self.commands.add(task)
        task.add_done_callback(self.commands.discard)

    def _restore(self):
        if self.outbox.exists():
            for saved in json.loads(self.outbox.read_text(encoding="utf-8")):
                mode = Mode(saved["mode"])
                path = Path(saved["path"])
                job = Job(
                    id=saved["id"], mode=mode, kind=saved["kind"],
                    name=saved["name"], media_type=saved["media_type"],
                    size=saved["size"], path=path, text=saved["text"],
                )
                status = saved["status"]
                if status in {"sending", "verifying"}:
                    status = "unconfirmed"
                elif status == "queued":
                    status = "paused"
                if status != "complete" and (path.parent != self.storage.outgoing or not path.is_file()):
                    continue
                entry = OutboxEntry(job, saved["binding"], status)
                self.items[job.id] = entry
                self._outgoing(entry)
        for item in reversed(self.storage.received(preview_chars=480)):
            self.changed.emit("item", {
                "id": "in:" + item.id, "direction": "incoming", "status": "complete",
                "mode": item.mode, "kind": item.kind, "name": item.name, "size": item.size,
                "transferred": item.size, "path": item.path, "text": item.text,
            })

    def _persist(self, *, exclude=None):
        atomic_save(self.outbox, json.dumps(
            [entry.record() for row_id, entry in self.items.items() if row_id != exclude],
            ensure_ascii=False,
        ).encode())

    def _outgoing(self, entry, *, transferred=0):
        job = entry.job
        self.changed.emit("item", {
            "id": job.id, "direction": "outgoing", "mode": job.mode,
            "kind": job.kind, "name": job.name, "text": job.text[:480] if job.text else None, "size": job.size,
            "status": entry.status, "transferred": transferred, "path": None,
        })

    async def _discover(self):
        if self.refreshing or self.connection and not self.connection.done():
            return
        self.refreshing = True
        self.changed.emit("connection", "scanning")
        try:
            devices = await self.usb.discover()
        except transport.TransportError as error:
            self.devices = {}
            self.changed.emit("devices", [])
            self.changed.emit("connection", error.code)
            return
        finally:
            self.refreshing = False
        self.devices = {device.stable_id: device for device in devices}
        self.changed.emit("devices", [(device.stable_id, device.label) for device in devices])
        self.changed.emit("connection", "select_device" if devices else "no_device")

    async def _new_pair_code(self, device_id, request_id):
        self._new_pairing(device_id, request_id, "code")

    async def _new_pair_qr(self, device_id, request_id):
        self._new_pairing(device_id, request_id, "qr")

    def _new_pairing(self, device_id, request_id, method):
        if self.storage is None:
            self._pairing_request_error(device_id, request_id, "storage_failed")
            return
        if self.connection and not self.connection.done():
            self._pairing_request_error(device_id, request_id, "pairing_busy")
            return
        if device_id not in self.devices:
            self._pairing_request_error(device_id, request_id, "no_device")
            return
        self._clear_pending_pairing()
        credential = f"{secrets.randbelow(1_000_000):06d}" if method == "code" else secrets.token_bytes(32)
        self.pending_pairing = PendingPairing(device_id, request_id, method, credential, time.monotonic() + CODE_TTL)
        self.pairing_timer = self.loop.call_later(CODE_TTL, self._expire_pairing, request_id)
        result = {
            "device": device_id, "request_id": request_id, "ttl": CODE_TTL,
            "expires_at": self.pending_pairing.expires,
        }
        if method == "code":
            result["code"] = credential
        else:
            result["payload"] = encode_pairing_qr(credential)
        self.changed.emit("pairing_" + method, result)

    def _pairing_request_error(self, device_id, request_id, reason):
        self.changed.emit("pairing_request_error", {
            "device": device_id, "request_id": request_id, "reason": reason,
        })

    def _clear_pending_pairing(self):
        self.pending_pairing = None
        if self.pairing_timer is not None:
            self.pairing_timer.cancel()
            self.pairing_timer = None

    def _expire_pairing(self, request_id):
        pending = self.pending_pairing
        if pending is not None and pending.request_id == request_id:
            self._clear_pending_pairing()
            self._pairing_request_error(pending.device, pending.request_id, "pairing_code_expired")

    async def _cancel_pair_code(self, device_id, request_id):
        pending = self.pending_pairing
        if pending is not None and (pending.device, pending.request_id) == (device_id, request_id):
            self._clear_pending_pairing()

    async def _pair_code(self, device_id, request_id):
        self._begin_pairing(device_id, request_id, "code")

    async def _pair_qr(self, device_id, request_id):
        self._begin_pairing(device_id, request_id, "qr")

    def _begin_pairing(self, device_id, request_id, method):
        pending = self.pending_pairing
        if pending is None or (pending.device, pending.request_id, pending.method) != (device_id, request_id, method):
            self._pairing_request_error(device_id, request_id, "pairing_code_expired")
            return
        self._clear_pending_pairing()
        if time.monotonic() >= pending.expires:
            self.changed.emit("connection", "pairing_code_expired")
            return
        if self.storage is None or self.connection and not self.connection.done():
            return
        device = self.devices.get(device_id)
        if device is None:
            self.changed.emit("connection", "no_device")
            return
        self.disconnect_requested = False
        self.connection = asyncio.create_task(self._supervise(device, None, pending=pending))

    async def _enqueue(self, device_id, mode, message, files):
        if self.storage is None:
            self.changed.emit("error", "storage_failed")
            self.changed.emit("enqueue_result", False)
            return
        count = bool(message) + len(files)
        outstanding = sum(entry.status != "complete" for entry in self.items.values())
        if not count or self.enqueued + outstanding + count > MAX_JOBS:
            self.changed.emit("error", "queue_full")
            self.changed.emit("enqueue_result", False)
            return
        pairing = self.pairings.load(device_id)
        if pairing is None:
            self.changed.emit("error", "needs_pair")
            self.changed.emit("enqueue_result", False)
            return
        self.enqueued += count
        epoch = self.disconnect_epoch
        staged = []
        try:
            if message:
                staged.append(await asyncio.to_thread(self.storage.stage_text, message, mode))
            for path, kind in files:
                staged.append(await asyncio.to_thread(self.storage.stage_file, Path(path), mode, kind=kind))
            status = "queued" if epoch == self.disconnect_epoch else "paused"
            for job in staged:
                self.items[job.id] = OutboxEntry(job, pairing.binding, status)
            self._persist()
        except (OSError, ValueError, RuntimeError, ProtocolError):
            for job in staged:
                self.items.pop(job.id, None)
                self.storage.discard_job(job)
            self.changed.emit("error", "stage_failed")
            self.changed.emit("enqueue_result", False)
            return
        finally:
            self.enqueued -= count
        for job in staged:
            self._outgoing(self.items[job.id])
        self.changed.emit("enqueue_result", True)
        self.wake.set()

    async def _connect(self, device_id):
        if self.storage is None or self.connection and not self.connection.done():
            return
        device = self.devices.get(device_id)
        if device is None:
            self.changed.emit("connection", "no_device")
            return
        try:
            pairing = self.pairings.load(device_id)
        except (PairingError, OSError):
            self.changed.emit("connection", "pairing_store_failed")
            return
        if pairing is None:
            self.changed.emit("connection", "needs_pair")
            return
        self.disconnect_requested = False
        self._clear_pending_pairing()
        self.connection = asyncio.create_task(self._supervise(device, pairing))

    async def _disconnect(self):
        connection = self.connection
        if connection is None or connection.done():
            return
        self.disconnect_requested = True
        if self.session is not None:
            await self.session.close()
        else:
            connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)

    async def _supervise(self, device, pairing, *, pending=None):
        state = "disconnected"
        writer = None
        try:
            while not self.disconnect_requested and not self.stop.is_set():
                self.changed.emit("connection", "pairing_code_connecting" if pending else "connecting")
                reader, writer = await self.usb.connect(device)
                if pending is not None:
                    remaining = pending.expires - time.monotonic()
                    if remaining <= 0:
                        state = "pairing_code_expired"
                        break
                    if pending.method == "code":
                        secret = await pair_client(reader, writer, pending.credential,
                                                   timeout=min(remaining, PAIRING_TIMEOUT))
                    else:
                        secret = pending.credential
                    pairing = Pairing(device.stable_id, secret)
                save = partial(self._save_pairing, pairing) if pending is not None else None
                session = Session(reader, writer, pairing.secret, self.storage, self._transfer_event,
                                  on_authenticated=save)
                self.session = session
                writer = None  # Session owns the stream from here.
                pending = None
                result = await self._run_session(session, pairing.binding)
                self.session = None
                if result != "drained" or self.stop.is_set():
                    break
        except transport.TransportError as error:
            state = error.code
        except PairingBackendError:
            state = "pairing_unavailable"
        except PairingProtocolError:
            state = "pairing_code_failed"
        except PairingError:
            state = "pairing_store_failed"
        except AuthenticationError:
            state = "authentication_failed"
        except ProtocolError:
            state = "protocol_failed"
        except (SessionCancelled, asyncio.CancelledError, ConnectionError):
            pass
        except (SessionError, OSError, ValueError, RuntimeError):
            state = "session_failed"
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (OSError, TimeoutError):
                    pass
            self.disconnect_epoch += 1
            changed = False
            for entry in self.items.values():
                if entry.status == "queued":
                    entry.status = "paused"
                    self._outgoing(entry)
                    changed = True
            if changed:
                try:
                    self._persist()
                except OSError:
                    self.changed.emit("error", "storage_failed")
            self.session = None
            self.changed.emit("connection", state)

    def _save_pairing(self, pairing):
        try:
            self.pairings.save(pairing)
        except OSError as error:
            raise PairingError("Windows could not store the new pairing") from error

    async def _run_session(self, session, binding):
        run = asyncio.create_task(session.run())
        ready = asyncio.create_task(session.wait_ready())
        pump = None
        try:
            await asyncio.wait((run, ready), return_when=asyncio.FIRST_COMPLETED)
            if run.done():
                return await run
            try:
                await ready
            except SessionError:
                return await run
            for entry in self.items.values():
                if entry.status == "paused" and entry.binding == binding:
                    entry.status = "queued"
                    self._outgoing(entry)
            self.changed.emit("connection", "connected")
            self.wake.set()
            pump = asyncio.create_task(self._pump(session, binding))
            await asyncio.wait((run, pump), return_when=asyncio.FIRST_COMPLETED)
            if run.done():
                return await run
            await pump
            return await run
        finally:
            if not run.done():
                await session.close()
            for task in (ready, pump):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (run, ready, pump) if task is not None),
                                 return_exceptions=True)
            self.progress_signals.clear()

    async def _pump(self, session, binding):
        while True:
            await self.wake.wait()
            self.wake.clear()
            for entry in list(self.items.values()):
                if (self.items.get(entry.job.id) is not entry
                        or entry.status != "queued" or entry.binding != binding):
                    continue
                self.admitting = entry.job.id
                try:
                    await session.send(entry.job)
                except DrainRequired:
                    return
                finally:
                    self.admitting = None
                if entry.status == "complete":
                    try:
                        self._persist()
                        self.storage.discard_job(entry.job)
                    except OSError:
                        self.changed.emit("error", "storage_failed")

    def _transfer_event(self, event):
        if self.session is None or event.generation != self.session.generation:
            return
        if event.direction == "outgoing":
            row_id, previous_id = event.job_id, None
        else:
            live_id = f"in:{event.generation}:{event.item_id.hex()}"
            row_id = "in:" + event.received_id if event.received_id else live_id
            previous_id = live_id if event.received_id else None
        entry = self.items.get(row_id)
        if entry is not None and event.status != entry.status:
            entry.status = event.status
            if entry.status == "complete" and entry.job.text:
                entry.job = replace(entry.job, text=entry.job.text[:480])
            try:
                self._persist()
            except OSError:
                self.changed.emit("error", "storage_failed")
        signal_key = (row_id, event.status)
        now = time.monotonic()
        if event.status in {"receiving", "sending"}:
            last = self.progress_signals.get(signal_key, 0)
            if event.transferred not in (0, event.size) and now - last < 0.14:
                return
            self.progress_signals[signal_key] = now
        else:
            for status in ("receiving", "sending"):
                self.progress_signals.pop((row_id, status), None)
        self.changed.emit("item", {
            "id": row_id, "previous_id": previous_id, "direction": event.direction, "status": event.status,
            "mode": event.mode, "kind": event.kind, "name": event.name,
            "size": event.size, "transferred": event.transferred,
            "path": event.path if event.direction == "incoming" and event.status == "complete" else None,
            "text": (entry.job.text[:480] if entry.job.text else None) if entry is not None
                    else (event.text[:480] if event.text else None),
        })

    async def _retry(self, row_id, device_id):
        if self.connection and not self.connection.done():
            return
        entry = self.items.get(row_id)
        pairing = self.pairings.load(device_id)
        if not entry or entry.status not in RETRYABLE or pairing is None or pairing.binding != entry.binding:
            self.changed.emit("error", "retry_peer")
            return
        entry.status = "queued"
        self._persist()
        self._outgoing(entry)
        await self._connect(device_id)

    async def _remove(self, row_id):
        entry = self.items.get(row_id)
        if entry is None or row_id == self.admitting or entry.status not in (RETRYABLE | {"queued", "paused"}):
            return
        self._persist(exclude=row_id)
        self.items.pop(row_id)
        self.storage.discard_job(entry.job)
        self.changed.emit("removed", row_id)

    async def _delete_received_message(self, row_id):
        if self.storage is None or not isinstance(row_id, str) or not row_id.startswith("in:"):
            return
        if row_id in self.deleting_messages:
            return
        self.deleting_messages.add(row_id)
        try:
            purged = await asyncio.to_thread(self.storage.delete_received_message, row_id.removeprefix("in:"))
            self.changed.emit("removed", row_id)
            self.changed.emit("notice" if purged else "error",
                              "message_deleted" if purged else "message_delete_pending")
        finally:
            self.deleting_messages.discard(row_id)

    async def _export(self, source, destination):
        def copy_file():
            destination_path = Path(destination)
            with tempfile.NamedTemporaryFile(dir=destination_path.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    with Path(source).open("rb") as input_file:
                        shutil.copyfileobj(input_file, output, 65536)
                    output.flush()
                    os.fsync(output.fileno())
                    output.close()
                    os.replace(temporary, destination_path)
                finally:
                    output.close()
                    temporary.unlink(missing_ok=True)

        await asyncio.to_thread(copy_file)
        self.changed.emit("notice", "exported")
