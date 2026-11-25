from pathlib import Path

import pytest


def test_stage_file_captures_selection_before_source_changes(tmp_path: Path):
    from usblinklab.protocol import Mode
    from usblinklab.storage import Storage

    source = tmp_path / "selected.bin"
    source.write_bytes(b"selected bytes")
    storage = Storage(tmp_path / "app")
    job = storage.stage_file(source, Mode.AES256GCM)
    source.write_bytes(b"edited after selection")

    assert job.size == 14
    assert job.name == "selected.bin"
    assert job.mode is Mode.AES256GCM
    assert job.path.read_bytes() == b"selected bytes"
    storage.discard_job(job)
    assert not job.path.exists()
    assert source.read_bytes() == b"edited after selection"


def test_windows_filenames_cannot_escape_storage(tmp_path: Path):
    from usblinklab.protocol import Mode, ProtocolError
    from usblinklab.storage import Storage

    source = tmp_path / "selected.bin"
    source.write_bytes(b"source")
    storage = Storage(tmp_path / "app")

    for name in ("../source", r"C:\source", r"\\host\source", "test:stream", "CON.txt", "a.", ""):
        with pytest.raises((ProtocolError, ValueError)):
            storage.stage_file(source, Mode.PLAINTEXT, name=name)

def test_staging_and_receive_disk_capacity_are_checked_before_creating_files(tmp_path, monkeypatch):
    from collections import namedtuple
    from usblinklab import storage as storage_module
    from usblinklab.protocol import Mode

    source = tmp_path / "selected.bin"
    source.write_bytes(b"sample")
    storage = storage_module.Storage(tmp_path / "app")
    disk_usage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(storage_module.shutil, "disk_usage", lambda _: disk_usage(10, 9, 1))

    with pytest.raises(OSError, match="Insufficient space"):
        storage.stage_file(source, Mode.PLAINTEXT)
    with pytest.raises(OSError, match="Insufficient space"):
        storage.begin_receive(6)
    assert not list(storage.outgoing.iterdir())
    assert not list(storage.incoming.iterdir())


def test_staged_png_preserves_the_original_representation(tmp_path):
    from base64 import b64decode
    from usblinklab.protocol import Mode
    from usblinklab.storage import Storage

    source = tmp_path / "synthetic.png"
    png = b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2p0AAAAASUVORK5CYII="
    )
    source.write_bytes(png)
    storage = Storage(tmp_path / "app")
    job = storage.stage_file(source, Mode.AES256GCM, kind="image")

    assert job.kind == "image"
    assert job.name == "synthetic.png"
    assert job.media_type == "image/png"
    assert job.path.read_bytes() == png


def commit_received(storage, data, *, kind="text"):
    import hashlib
    from usblinklab.protocol import Mode

    transfer = storage.begin_receive(len(data))
    transfer.write(data)
    return storage.commit_receive(
        transfer, mode=Mode.AES256GCM, kind=kind, name="" if kind == "text" else "sample.bin",
        media_type="text/plain; charset=utf-8" if kind == "text" else "application/octet-stream",
        size=len(data), sha256=hashlib.sha256(data).hexdigest(),
    )


def test_delete_received_message_purges_only_committed_text(tmp_path):
    from usblinklab.storage import Storage

    storage = Storage(tmp_path / "app")
    message = commit_received(storage, b"committed text")
    other = commit_received(storage, b"another text")
    attachment = commit_received(storage, b"attachment", kind="file")

    for invalid in ("../outside", "in:generation:wire-id", attachment.id):
        with pytest.raises(ValueError):
            storage.delete_received_message(invalid)

    assert storage.delete_received_message(message.id)
    assert not message.path.parent.exists()
    recovered = Storage(tmp_path / "app").received()
    assert [item.id for item in recovered] == [other.id, attachment.id]
    assert attachment.path.read_bytes() == b"attachment"
    with pytest.raises((ValueError, FileNotFoundError)):
        storage.delete_received_message(message.id)


def test_delete_received_message_recovers_after_interrupted_cleanup(tmp_path, monkeypatch):
    from usblinklab.storage import Storage

    root = tmp_path / "app"
    storage = Storage(root)
    message = commit_received(storage, b"recoverable cleanup")
    unlink = Path.unlink

    def locked_content(path, *args, **kwargs):
        if path.name == "content":
            raise PermissionError("fixture content is locked")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", locked_content)
        assert not storage.delete_received_message(message.id)
        assert storage.received() == []
        assert not message.path.exists()
        assert Storage(root).received() == []

    reopened = Storage(root)
    assert reopened.received() == []
    assert list(reopened.complete.iterdir()) == []


def test_delete_received_message_preserves_damaged_record(tmp_path):
    import json
    from usblinklab.storage import Storage

    storage = Storage(tmp_path / "app")
    message = commit_received(storage, b"preserved text")
    record = message.path.parent / "metadata.json"
    record.write_text(json.dumps({"id": message.id, "kind": "text"}), encoding="utf-8")

    with pytest.raises(ValueError):
        storage.delete_received_message(message.id)
    assert message.path.read_bytes() == b"preserved text"
    assert record.exists()
