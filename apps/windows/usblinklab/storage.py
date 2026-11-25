"""Own outgoing snapshots and committed receive records on one volume."""

from __future__ import annotations

import codecs
import json
import mimetypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from .protocol import CHUNK_SIZE, MAX_FILE_SIZE, MAX_TEXT_SIZE, Mode, validate_filename


TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"
_DELETED_RECEIVED = ".deleted-"


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    mode: Mode
    kind: str
    name: str
    media_type: str
    size: int
    path: Path
    text: str | None = None


@dataclass(frozen=True, slots=True)
class ReceivedItem:
    id: str
    mode: Mode
    kind: str
    name: str
    media_type: str
    size: int
    path: Path
    sha256: str
    timestamp: str
    text: str | None = None


@dataclass(slots=True)
class IncomingFile:
    path: Path
    stream: BinaryIO

    def write(self, data: bytes) -> None:
        if self.stream.write(data) != len(data):
            raise OSError("Incomplete write to receive storage")

    def abort(self) -> None:
        try:
            self.stream.close()
        finally:
            self.path.unlink(missing_ok=True)


class Storage:
    """Synchronous filesystem operations intended for a worker thread."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.outgoing = self.root / "outgoing"
        self.incoming = self.root / "incoming"
        self.complete = self.root / "received"
        for directory in (self.outgoing, self.incoming, self.complete):
            directory.mkdir(parents=True, exist_ok=True)
        for directory in self.complete.glob(_DELETED_RECEIVED + "*"):
            received_id = directory.name.removeprefix(_DELETED_RECEIVED)
            try:
                if self._valid_received_id(received_id):
                    self._purge_deleted_record(directory)
            except OSError:
                continue

    def stage_text(self, text: str, mode: Mode) -> Job:
        if len(text) > MAX_TEXT_SIZE:
            raise ValueError("Text exceeds the 1 MiB UTF-8 limit")
        data = text.encode("utf-8")
        if len(data) > MAX_TEXT_SIZE:
            raise ValueError("Text exceeds the 1 MiB UTF-8 limit")
        mode = Mode(mode)
        self._check_space(self.outgoing, len(data))
        item_id = uuid4().hex
        path = self.outgoing / f"{item_id}.stage"
        stream = path.open("xb")
        try:
            with stream:
                stream.write(data)
                self._sync(stream)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return Job(item_id, mode, "text", "", TEXT_MEDIA_TYPE, len(data), path, text)

    def stage_file(
        self,
        path: Path,
        mode: Mode,
        *,
        kind: str = "file",
        name: str | None = None,
        media_type: str | None = None,
    ) -> Job:
        path = Path(path)
        mode = Mode(mode)
        if kind not in ("file", "image"):
            raise ValueError("Selected attachment must be an image or file")
        if name is None:
            name = path.name
        validate_filename(name)
        if media_type is None:
            media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not 1 <= len(media_type) <= 127 or any(not 32 <= ord(char) <= 126 for char in media_type):
            raise ValueError("Invalid attachment media type")
        if kind == "image" and not media_type.startswith("image/"):
            raise ValueError("Could not identify an image media type")

        item_id = uuid4().hex
        staged = self.outgoing / f"{item_id}.stage"
        if not path.is_file():
            raise ValueError("Select a regular local file")
        with path.open("rb") as source:
            initial = os.fstat(source.fileno())
            size = initial.st_size
            if size > MAX_FILE_SIZE:
                raise ValueError("Attachment exceeds the 1 GiB limit")
            self._check_space(self.outgoing, size)
            destination = staged.open("xb")
            try:
                with destination:
                    remaining = size
                    while remaining:
                        data = source.read(min(CHUNK_SIZE, remaining))
                        if not data:
                            raise OSError("The selected file changed during staging")
                        destination.write(data)
                        remaining -= len(data)
                    if source.read(1) or self._file_stamp(source) != self._stat_stamp(initial):
                        raise OSError("The selected file changed during staging")
                    self._sync(destination)
            except BaseException:
                staged.unlink(missing_ok=True)
                raise
        return Job(item_id, mode, kind, name, media_type, size, staged)

    def discard_job(self, job: Job) -> None:
        if job.path.parent != self.outgoing or job.path.name != f"{job.id}.stage":
            raise ValueError("Job is outside this application's staging storage")
        job.path.unlink(missing_ok=True)

    def begin_receive(self, size: int) -> IncomingFile:
        self._check_space(self.incoming, size)
        stream = tempfile.NamedTemporaryFile(mode="w+b", suffix=".part", dir=self.incoming, delete=False)
        return IncomingFile(Path(stream.name), stream)

    def commit_receive(
        self,
        temporary: IncomingFile,
        *,
        mode: Mode,
        kind: str,
        name: str,
        media_type: str,
        size: int,
        sha256: str,
    ) -> ReceivedItem:
        if temporary.path.parent != self.incoming:
            raise ValueError("File is outside this application's receive storage")
        stream = temporary.stream
        if stream.tell() != size:
            raise OSError("Receive storage size differs from the verified size")
        text = None
        if kind == "text":
            stream.seek(0)
            text = stream.read(size).decode("utf-8")
        self._sync(stream)
        stream.close()

        item_id = uuid4().hex
        directory = self.complete / item_id
        directory.mkdir()
        target = directory / "content"
        pending_record = directory / "metadata.part"
        record = directory / "metadata.json"
        timestamp = datetime.now(timezone.utc).isoformat()
        metadata = {
            "id": item_id,
            "mode": int(mode),
            "kind": kind,
            "name": name,
            "media_type": media_type,
            "size": size,
            "sha256": sha256,
            "timestamp": timestamp,
        }
        try:
            temporary.path.rename(target)
            with pending_record.open("xb") as stream:
                stream.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
                self._sync(stream)
            pending_record.rename(record)
        except BaseException:
            pending_record.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return ReceivedItem(item_id, mode, kind, name, media_type, size, target, sha256, timestamp, text)

    def received(self, *, preview_chars: int | None = None) -> list[ReceivedItem]:
        items = []
        for record in self.complete.glob("*/metadata.json"):
            if record.parent.name.startswith(_DELETED_RECEIVED):
                continue
            try:
                items.append(self._read_received(record, preview_chars))
            except (OSError, KeyError, TypeError, ValueError):
                continue
        return sorted(items, key=lambda item: item.timestamp)

    @staticmethod
    def _read_received(record: Path, preview_chars: int | None) -> ReceivedItem:
        with record.open("rb") as stream:
            metadata = json.loads(stream.read(4097))
        if not isinstance(metadata, dict) or metadata["id"] != record.parent.name:
            raise ValueError("Invalid received item identity")
        string_fields = ("kind", "name", "media_type", "sha256", "timestamp")
        if any(not isinstance(metadata[key], str) for key in string_fields):
            raise ValueError("Invalid received item fields")
        if type(metadata["size"]) is not int or type(metadata["mode"]) is not int:
            raise ValueError("Invalid received item size or protection mode")
        path = record.parent / "content"
        if path.stat().st_size != metadata["size"]:
            raise ValueError("Invalid received item size")
        metadata["mode"] = Mode(metadata["mode"])
        text = None
        if metadata["kind"] == "text":
            if not 0 <= metadata["size"] <= MAX_TEXT_SIZE:
                raise ValueError("Invalid received text size")
            with path.open("rb") as stream:
                if preview_chars is None:
                    text = stream.read(MAX_TEXT_SIZE + 1).decode("utf-8")
                else:
                    preview = stream.read(preview_chars * 4)
                    text = codecs.getincrementaldecoder("utf-8")().decode(preview)[:preview_chars]
        return ReceivedItem(path=path, text=text, **metadata)

    def delete_received_message(self, item_id: str) -> bool:
        """Remove one committed text row. False means disk cleanup will retry on launch."""
        if not self._valid_received_id(item_id):
            raise ValueError("Invalid received item identity")
        directory = self.complete / item_id
        record = directory / "metadata.json"
        payload = directory / "content"
        if (directory.resolve(strict=True) != directory or directory.is_junction() or
                record.is_symlink() or payload.is_symlink() or
                not record.is_file() or not payload.is_file()):
            raise ValueError("Received message is outside app-owned storage")
        try:
            item = self._read_received(record, 0)
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid received message metadata") from exc
        if item.kind != "text":
            raise ValueError("Only committed received text can be deleted")

        # Keep history consistent across crashes and failed file cleanup.
        deleted = directory.with_name(_DELETED_RECEIVED + item_id)
        directory.rename(deleted)
        try:
            self._purge_deleted_record(deleted)
        except OSError:
            return False
        return True

    @staticmethod
    def _valid_received_id(value: str) -> bool:
        try:
            identifier = UUID(value)
        except (AttributeError, TypeError, ValueError):
            return False
        return identifier.version == 4 and identifier.hex == value

    @staticmethod
    def _purge_deleted_record(directory: Path) -> None:
        if (directory.is_symlink() or directory.is_junction() or not directory.is_dir()
                or directory.resolve(strict=True) != directory):
            raise OSError("Deleted message is outside app-owned storage")
        (directory / "content").unlink(missing_ok=True)
        (directory / "metadata.json").unlink(missing_ok=True)
        directory.rmdir()

    @staticmethod
    def _check_space(path: Path, size: int) -> None:
        if shutil.disk_usage(path).free < size:
            raise OSError("Insufficient space in application storage")

    @staticmethod
    def _sync(stream: BinaryIO) -> None:
        stream.flush()
        os.fsync(stream.fileno())

    @staticmethod
    def _file_stamp(stream: BinaryIO) -> tuple[int, int, int]:
        return Storage._stat_stamp(os.fstat(stream.fileno()))

    @staticmethod
    def _stat_stamp(info: os.stat_result) -> tuple[int, int, int]:
        return info.st_size, info.st_mtime_ns, info.st_ctime_ns