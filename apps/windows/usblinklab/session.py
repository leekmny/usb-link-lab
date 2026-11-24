"""One full-duplex ULAB connection with a serialized application writer."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .protocol import (
    CHUNK_SIZE,
    MAX_BODY_BYTES,
    MAX_FRAME_COUNT,
    MAX_ITEM_IDS,
    MAX_JSON_SIZE,
    EndOfStream,
    Frame,
    FrameType,
    Mode,
    ProtocolError,
    decode_body,
    encode_body,
    encode_frame,
    handshake,
    read_frame,
)
from .storage import IncomingFile, Job, ReceivedItem, Storage


ZERO_ID = bytes(16)
ITEM_TIMEOUT = 60.0
PROGRESS_INTERVAL = 0.15
logger = logging.getLogger(__name__)


class SessionError(Exception):
    """An authenticated connection is unusable."""


class SessionCancelled(SessionError):
    """The user stopped this connection and its active transfers."""


class DrainRequired(SessionError):
    """The job was never admitted and can be sent on a fresh session."""


@dataclass(frozen=True, slots=True)
class TransferEvent:
    generation: str
    direction: str
    status: str
    item_id: bytes
    job_id: str | None
    mode: Mode
    kind: str
    name: str
    media_type: str
    size: int
    transferred: int
    path: Path | None = None
    text: str | None = None
    detail: str = ""
    received_id: str | None = None
    timestamp: str | None = None


@dataclass(slots=True)
class _Outgoing:
    id: bytes
    job: Job
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    phase: str = "opening"
    transferred: int = 0
    deadline: float = 0
    last_report: float = 0


@dataclass(slots=True)
class _Incoming:
    id: bytes
    mode: Mode
    kind: str = ""
    name: str = ""
    media_type: str = ""
    size: int = 0
    transferred: int = 0
    chunks: int = 0
    deadline: float = 0
    last_report: float = 0
    digest: object = field(default_factory=hashlib.sha256)
    file: IncomingFile | None = None
    committed: ReceivedItem | None = None
    reported_complete: bool = False


async def _disk(operation, *args, cancelled_result=None, **kwargs):
    """Wait for an in-flight filesystem operation before touching its file again."""
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        result, = await asyncio.gather(worker, return_exceptions=True)
        if cancelled_result is not None and not isinstance(result, BaseException):
            await asyncio.to_thread(cancelled_result, result)
        raise


class Session:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        psk: bytes,
        storage: Storage,
        callback: Callable[[TransferEvent], None],
        *,
        client: bool = True,
    ):
        self.reader = reader
        self.writer = writer
        # Frame deadlines and drain barriers use completion of the entire local write.
        self.writer.transport.set_write_buffer_limits(high=0, low=0)
        self._psk = psk
        self.storage = storage
        self.callback = callback
        self.client = client
        self.generation = uuid4().hex

        self._write_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self._activity = asyncio.Event()
        self._aborted = asyncio.Event()
        self._out_keys = self._in_keys = None
        self._run_started = self._closing = False
        self._failure: Exception | None = None

        self._send_sequence = self._receive_sequence = 0
        self._sent_bytes = self._received_bytes = 0
        self._local_ids: set[bytes] = set()
        self._peer_ids: set[bytes] = set()
        self._peer_completed = 0
        self._outgoing: _Outgoing | None = None
        self._incoming: _Incoming | None = None
        self._sending_task: asyncio.Task | None = None

        self._draining = False
        self._request_written = self._peer_request = False
        self._ready_written = self._peer_ready = False
        self._drain_deadline: float | None = None

    async def run(self) -> str:
        """Authenticate and dispatch frames until drain, cancellation or failure."""
        if self._run_started or self._closing:
            raise SessionError("Session has already run or closed")
        self._run_started = True
        tasks = []
        outcome = None
        try:
            self._out_keys, self._in_keys = await handshake(
                self.reader, self.writer, self._psk, client=self.client
            )
            self._ready.set()
            read_task = asyncio.create_task(self._read_loop())
            watch_task = asyncio.create_task(self._watch_activity())
            abort_task = asyncio.create_task(self._aborted.wait())
            tasks = [read_task, watch_task, abort_task]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if abort_task in done:
                raise self._failure
            if watch_task in done:
                watch_task.result()
            outcome = read_task.result()
            return outcome
        except EndOfStream as exc:
            self._failure = self._failure or SessionError("Connection closed")
            raise self._failure from exc
        except (OSError, EOFError) as exc:
            self._failure = self._failure or SessionError("Connection or local storage was interrupted")
            raise self._failure from exc
        except Exception as exc:
            self._failure = self._failure or exc
            raise self._failure
        finally:
            try:
                self._closing = True
                self._ready.set()
                if outcome == "drained":
                    self.writer.close()
                else:
                    self.writer.transport.abort()
                for task in tasks:
                    task.cancel()
                if self._sending_task is not None:
                    if self._outgoing is not None:
                        self._sending_task.cancel()
                    tasks.append(self._sending_task)
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await self._interrupt_items()
                try:
                    await asyncio.wait_for(self.writer.wait_closed(), timeout=2)
                except (OSError, TimeoutError):
                    pass
            finally:
                self._stopped.set()

    async def wait_ready(self) -> None:
        await self._ready.wait()
        if self._out_keys is None or self._closing:
            raise SessionError("Session is not authenticated or has closed") from self._failure

    async def close(self) -> None:
        """Cancel the session and both transfers. Unadmitted jobs stay staged."""
        if not self._closing:
            self._abort(SessionCancelled("Session cancelled"))
        if self._run_started:
            await self._stopped.wait()

    async def send(self, job: Job) -> bytes:
        """Send a staged job and return its fresh wire ID after authenticated completion."""
        if self._draining:
            raise DrainRequired("Pending job requires a fresh connection")
        await self.wait_ready()
        if self._sending_task is not None:
            raise SessionError("Another outgoing job is active")
        task = asyncio.create_task(self._perform_send(job))
        self._sending_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise SessionError("Outgoing transfer interrupted") from self._failure
            await self.close()
            raise
        finally:
            self._sending_task = None

    async def request_drain(self) -> None:
        await self.wait_ready()
        self._draining = True
        self._notify_activity()
        async with self._write_lock:
            if not self._request_written:
                await self._write_locked(
                    FrameType.DRAIN, Mode.PLAINTEXT, ZERO_ID, 0, encode_body({"stage": "request"})
                )
                self._request_written = True
        await self._maybe_ready()

    async def _perform_send(self, job: Job) -> bytes:
        item = None
        request_needed = False
        try:
            async with self._write_lock:
                if not self._draining and self._fits_job(job):
                    item = _Outgoing(self._new_id(), job)
                    self._outgoing = item
                    self._refresh(item)
                    self._report_out(item, "sending")
                    item.phase = "wait_ready"
                    await self._write_locked(
                        FrameType.OPEN, job.mode, item.id, 0, encode_body({"mode": int(job.mode)})
                    )
                    self._refresh(item)
                    if len(self._local_ids) == MAX_ITEM_IDS and not self._draining:
                        self._draining = request_needed = True
                elif not self._draining:
                    self._draining = request_needed = True
            if request_needed:
                await self.request_drain()
            if item is None:
                raise DrainRequired("Session limit reached before this job was admitted")
            await item.ready.wait()

            metadata = {
                "kind": job.kind,
                "name": job.name,
                "media_type": job.media_type,
                "size": job.size,
                "chunk_size": CHUNK_SIZE,
            }
            await self._send_frame(FrameType.META, job.mode, item.id, 0, encode_body(metadata))
            self._refresh(item)
            digest = hashlib.sha256()
            chunks = 0
            source = await _disk(job.path.open, "rb", cancelled_result=lambda stream: stream.close())
            try:
                while item.transferred < job.size:
                    expected = min(CHUNK_SIZE, job.size - item.transferred)
                    data = await _disk(source.read, expected)
                    if len(data) != expected:
                        raise OSError("Staged bytes changed while sending")
                    await self._send_frame(FrameType.DATA, job.mode, item.id, chunks, data)
                    digest.update(data)
                    chunks += 1
                    item.transferred += len(data)
                    self._refresh(item)
                    if self._should_report(item):
                        self._report_out(item, "sending")
                if await _disk(source.read, 1):
                    raise OSError("Staged bytes changed while sending")
            finally:
                await _disk(source.close)

            body = encode_body({"bytes": item.transferred, "chunks": chunks, "sha256": digest.hexdigest()})
            async with self._write_lock:
                if self._closing:
                    raise SessionError("Session closed before END")
                item.phase = "wait_complete"
                self._report_out(item, "verifying")
                await self._write_locked(FrameType.END, job.mode, item.id, chunks, body)
                if not item.complete.is_set():
                    self._refresh(item)
            await item.complete.wait()
            return item.id
        except DrainRequired:
            raise
        except (OSError, ProtocolError, SessionError) as exc:
            if item is not None and item.complete.is_set():
                return item.id
            error = SessionError("Could not send staged item")
            self._abort(error)
            raise error from exc

    async def _read_loop(self) -> str:
        while True:
            try:
                frame = await read_frame(self.reader, self._in_keys, self._receive_sequence)
            except EndOfStream:
                if not self.client and self._ready_written and self._peer_ready:
                    return "drained"
                raise
            if self._closing:
                raise self._failure or SessionError("Session closed")
            self._receive_sequence += 1
            self._received_bytes += len(frame.body)
            if self._received_bytes > MAX_BODY_BYTES:
                raise ProtocolError("Inbound session byte limit exceeded")
            if frame.type is FrameType.ERROR:
                raise SessionError("Peer closed after a protocol error")
            try:
                await self._handle_frame(frame)
            except ProtocolError:
                try:
                    await self._send_frame(
                        FrameType.ERROR, Mode.PLAINTEXT, ZERO_ID, 0, encode_body({"code": "protocol_error"})
                    )
                except (OSError, ProtocolError, SessionError):
                    pass
                raise
            if self.client and self._ready_written and self._peer_ready:
                return "drained"

    async def _handle_frame(self, frame: Frame) -> None:
        if frame.type is FrameType.OPEN:
            await self._receive_open(frame)
        elif frame.type is FrameType.ACK:
            await self._receive_ack(frame)
        elif frame.type is FrameType.DRAIN:
            await self._receive_drain(frame)
        else:
            await self._receive_content(frame)

    async def _receive_open(self, frame: Frame) -> None:
        if self._peer_request or self._incoming is not None or frame.item_id in self._peer_ids:
            raise ProtocolError("Unexpected incoming OPEN")
        if len(self._peer_ids) >= MAX_ITEM_IDS:
            raise ProtocolError("Peer item ID limit exceeded")
        self._peer_ids.add(frame.item_id)
        self._incoming = item = _Incoming(frame.item_id, frame.mode)
        self._refresh(item)
        await self._send_frame(FrameType.ACK, frame.mode, frame.item_id, 0, encode_body({"stage": "ready"}))
        self._refresh(item)

    async def _receive_ack(self, frame: Frame) -> None:
        item = self._outgoing
        if item is None or (frame.item_id, frame.mode) != (item.id, item.job.mode):
            raise ProtocolError("ACK does not match the active outgoing item")
        stage = decode_body(frame)["stage"]
        if stage == "ready" and item.phase == "wait_ready":
            item.phase = "sending"
            item.ready.set()
            self._refresh(item)
        elif stage == "complete" and item.phase == "wait_complete":
            item.phase = "complete"
            item.complete.set()
            self._outgoing = None
            self._report_out(item, "complete")
            self._notify_activity()
            await self._maybe_ready()
        else:
            raise ProtocolError("Unexpected or repeated ACK stage")

    async def _receive_content(self, frame: Frame) -> None:
        item = self._incoming
        if item is None or (frame.item_id, frame.mode) != (item.id, item.mode):
            raise ProtocolError("Content does not match the active incoming item")
        if frame.type is FrameType.META and item.file is None:
            data = decode_body(frame)
            item.kind, item.name, item.media_type, item.size = (
                data["kind"], data["name"], data["media_type"], data["size"]
            )
            item.file = await _disk(
                self.storage.begin_receive, item.size, cancelled_result=lambda file: file.abort()
            )
            self._refresh(item)
            self._report_in(item, "receiving")
        elif frame.type is FrameType.DATA and item.file is not None:
            if frame.index != item.chunks or len(frame.body) != min(CHUNK_SIZE, item.size - item.transferred):
                raise ProtocolError("Unexpected DATA length or index")
            await _disk(item.file.write, frame.body)
            item.digest.update(frame.body)
            item.transferred += len(frame.body)
            item.chunks += 1
            self._refresh(item)
            if self._should_report(item):
                self._report_in(item, "receiving")
        elif frame.type is FrameType.END and item.file is not None:
            await self._receive_end(item, frame)
        else:
            raise ProtocolError("Unexpected content frame")

    async def _receive_end(self, item: _Incoming, frame: Frame) -> None:
        data = decode_body(frame)
        if (
            data["bytes"] != item.size
            or item.transferred != item.size
            or data["chunks"] != item.chunks
            or frame.index != item.chunks
            or data["sha256"] != item.digest.hexdigest()
        ):
            raise ProtocolError("Content size, chunk count or SHA-256 verification failed")
        self._report_in(item, "verifying")
        self._refresh(item)

        def retain_commit(committed: ReceivedItem) -> None:
            item.committed = committed

        try:
            item.committed = await _disk(
                self.storage.commit_receive,
                item.file,
                mode=item.mode,
                kind=item.kind,
                name=item.name,
                media_type=item.media_type,
                size=item.size,
                sha256=data["sha256"],
                cancelled_result=retain_commit,
            )
        except UnicodeDecodeError as exc:
            raise ProtocolError("Received text is not valid UTF-8") from exc
        self._report_in(item, "complete")
        item.reported_complete = True
        self._refresh(item)
        async with self._write_lock:
            await self._write_locked(
                FrameType.ACK, item.mode, item.id, 0, encode_body({"stage": "complete"})
            )
            self._peer_completed += 1
            self._incoming = None
            self._notify_activity()
        await self._maybe_ready()

    async def _receive_drain(self, frame: Frame) -> None:
        stage = decode_body(frame)["stage"]
        if stage == "request" and not self._peer_request:
            self._peer_request = True
            await self.request_drain()
        elif (
            stage == "ready"
            and self._peer_request
            and self._request_written
            and not self._peer_ready
            and self._outgoing is None
            and self._incoming is None
        ):
            self._peer_ready = True
            self._notify_activity()
            await self._maybe_ready()
        else:
            raise ProtocolError("Unexpected or repeated DRAIN stage")

    async def _maybe_ready(self) -> None:
        if not self._peer_request or not self._request_written or self._ready_written:
            return
        async with self._write_lock:
            if self._outgoing is None and self._incoming is None and not self._ready_written:
                await self._write_locked(
                    FrameType.DRAIN, Mode.PLAINTEXT, ZERO_ID, 0, encode_body({"stage": "ready"})
                )
                self._ready_written = True
                self._notify_activity()

    async def _send_frame(self, type_: FrameType, mode: Mode, item_id: bytes, index: int, body: bytes) -> None:
        async with self._write_lock:
            await self._write_locked(type_, mode, item_id, index, body)

    async def _write_locked(self, type_: FrameType, mode: Mode, item_id: bytes, index: int, body: bytes) -> None:
        if self._closing:
            raise SessionError("Session closed before frame write")
        if self._send_sequence >= MAX_FRAME_COUNT or self._sent_bytes + len(body) > MAX_BODY_BYTES:
            raise ProtocolError("Outbound session budget exhausted")
        record = encode_frame(Frame(type_, mode, self._send_sequence, item_id, index, body), self._out_keys)
        self._send_sequence += 1
        self._sent_bytes += len(body)
        self.writer.write(record)
        await self.writer.drain()

    def _new_id(self) -> bytes:
        while True:
            item_id = secrets.token_bytes(16)
            if item_id != ZERO_ID and item_id not in self._local_ids:
                self._local_ids.add(item_id)
                return item_id

    def _fits_job(self, job: Job) -> bool:
        remaining_peer_items = MAX_ITEM_IDS - self._peer_completed
        chunks = (job.size + CHUNK_SIZE - 1) // CHUNK_SIZE
        control_frames = 6 + 2 * remaining_peer_items
        return (
            len(self._local_ids) < MAX_ITEM_IDS
            and self._sent_bytes + job.size + control_frames * MAX_JSON_SIZE <= MAX_BODY_BYTES
            and self._send_sequence + chunks + control_frames <= MAX_FRAME_COUNT
        )

    def _refresh(self, item: _Incoming | _Outgoing) -> None:
        item.deadline = asyncio.get_running_loop().time() + ITEM_TIMEOUT
        self._notify_activity()

    def _notify_activity(self) -> None:
        if self._draining and self._outgoing is None and self._incoming is None:
            if self._drain_deadline is None:
                self._drain_deadline = asyncio.get_running_loop().time() + ITEM_TIMEOUT
        else:
            self._drain_deadline = None
        self._activity.set()

    async def _watch_activity(self) -> None:
        while True:
            self._activity.clear()
            deadlines = [item.deadline for item in (self._outgoing, self._incoming) if item is not None]
            if self._drain_deadline is not None:
                deadlines.append(self._drain_deadline)
            deadline = min(deadlines, default=None)
            if deadline is not None and deadline <= asyncio.get_running_loop().time():
                raise SessionError("Transfer or session drain timed out")
            try:
                async with asyncio.timeout_at(deadline):
                    await self._activity.wait()
            except TimeoutError as exc:
                raise SessionError("Transfer or session drain timed out") from exc

    def _abort(self, error: Exception) -> None:
        self._failure = self._failure or error
        self._closing = True
        self._ready.set()
        self._aborted.set()
        self.writer.transport.abort()

    async def _interrupt_items(self) -> None:
        item = self._outgoing
        if item is not None:
            if item.phase == "wait_complete":
                self._report_out(item, "unconfirmed", "The peer may already have saved this item")
            else:
                self._report_out(item, "interrupted", "The outgoing transfer was interrupted")
            self._outgoing = None
        incoming = self._incoming
        if incoming is not None:
            if incoming.committed is not None:
                if not incoming.reported_complete:
                    self._report_in(incoming, "complete")
            else:
                if incoming.file is not None:
                    try:
                        await _disk(incoming.file.abort)
                    except OSError:
                        logger.warning("Could not discard interrupted receive file")
                if incoming.kind:
                    self._report_in(incoming, "interrupted", "The incoming transfer was interrupted")
            self._incoming = None

    @staticmethod
    def _should_report(item: _Incoming | _Outgoing) -> bool:
        now = asyncio.get_running_loop().time()
        if now - item.last_report >= PROGRESS_INTERVAL:
            item.last_report = now
            return True
        return False

    def _report_out(self, item: _Outgoing, status: str, detail: str = "") -> None:
        job = item.job
        self._report(TransferEvent(
            self.generation, "outgoing", status, item.id, job.id, job.mode, job.kind,
            job.name, job.media_type, job.size, item.transferred, job.path, job.text, detail,
        ))

    def _report_in(self, item: _Incoming, status: str, detail: str = "") -> None:
        result = item.committed
        self._report(TransferEvent(
            self.generation, "incoming", status, item.id, None, item.mode, item.kind,
            item.name, item.media_type, item.size, item.transferred,
            result.path if result else None, result.text if result else None, detail,
            result.id if result else None, result.timestamp if result else None,
        ))

    def _report(self, event: TransferEvent) -> None:
        try:
            self.callback(event)
        except Exception:
            logger.warning("Could not deliver a transfer event")