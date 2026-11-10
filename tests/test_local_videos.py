"""Durable local upload pipeline, legacy migration and failure recovery."""
from types import SimpleNamespace

import pytest

from app import local_videos


@pytest.fixture
def isolated_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(local_videos, "DATA", tmp_path)
    monkeypatch.setattr(local_videos, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(local_videos, "WORK", tmp_path / "work")
    monkeypatch.setattr(local_videos, "READY", tmp_path / "videos")
    monkeypatch.setattr(local_videos, "MARKERS", tmp_path / "markers")
    monkeypatch.setattr(local_videos, "JOBS", tmp_path / "jobs.json")
    monkeypatch.setattr(
        local_videos.shutil, "disk_usage",
        lambda path: SimpleNamespace(free=60 * 1024**3),
    )
    local_videos.init()
    return tmp_path


def _fake_normalize(source, target, max_bytes=None, on_progress=None):
    assert source.exists()
    assert max_bytes == local_videos.LOCAL_MAX_BYTES
    target.write_bytes(bytes.fromhex("000000186674797069736f6d") + b"x" * 1200)
    return "copy"


def test_upload_queued_then_published_atomically(monkeypatch, isolated_queue):
    monkeypatch.setattr(local_videos, "normalize_video", _fake_normalize)
    incoming = isolated_queue / "uploaded.webm"
    incoming.write_bytes(b"original bytes")
    job = local_videos.enqueue(incoming, "My.video.webm")
    assert job["state"] == "queued"
    assert not incoming.exists()
    assert len(local_videos.status()) == 1
    assert list(local_videos.READY.iterdir()) == []
    assert local_videos.process_one()
    assert local_videos.status() == []
    ready = list(local_videos.READY.glob("*.mp4"))
    assert len(ready) == 1
    assert local_videos.is_ready(ready[0])
    assert not list(local_videos.INBOX.iterdir())
    assert not list(local_videos.WORK.iterdir())


def test_failed_conversion_keeps_original_for_retry(monkeypatch, isolated_queue):
    incoming = isolated_queue / "uploaded.mkv"
    incoming.write_bytes(b"original")
    job = local_videos.enqueue(incoming, "holiday.mkv")

    def fail(*args, **kw):
        raise ValueError("Неизвестный кодек")

    monkeypatch.setattr(local_videos, "normalize_video", fail)
    assert local_videos.process_one()
    assert local_videos.status()[0]["state"] == "failed"
    assert "Неизвестный кодек" in local_videos.status()[0]["error"]
    source = next(local_videos.INBOX.iterdir())
    assert source.read_bytes() == b"original"
    assert list(local_videos.READY.glob("*.mp4")) == []
    assert local_videos.retry(job["id"])
    monkeypatch.setattr(local_videos, "normalize_video", _fake_normalize)
    assert local_videos.process_one()
    assert not source.exists()
    assert local_videos.status() == []
    assert len(list(local_videos.READY.glob("*.mp4"))) == 1


def test_delete_failed_job_does_not_leave_original(monkeypatch, isolated_queue):
    incoming = isolated_queue / "badvideo.mov"
    incoming.write_bytes(b"not really video")
    job = local_videos.enqueue(incoming, "bad.mov")
    monkeypatch.setattr(local_videos, "normalize_video",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("broken")))
    local_videos.process_one()
    assert not local_videos.retry("wrong")
    assert local_videos.delete_job(job["id"])
    assert local_videos.status() == []
    assert list(local_videos.INBOX.iterdir()) == []


def test_preexisting_uploads_are_migrated_in_background(monkeypatch, isolated_queue):
    monkeypatch.setattr(local_videos, "normalize_video", _fake_normalize)
    legacy = local_videos.READY / "old-film.m4v"
    legacy.write_bytes(b"old video")
    local_videos.discover_legacy()
    local_videos.discover_legacy()
    queue = local_videos.status()
    assert len(queue) == 1
    assert queue[0]["origin"] == "videos"
    assert not local_videos.is_ready(legacy)
    assert local_videos.process_one()
    assert not legacy.exists()
    ready = list(local_videos.READY.glob("*.mp4"))
    assert len(ready) == 1 and local_videos.is_ready(ready[0])
    local_videos.discover_legacy()
    assert local_videos.status() == []


def test_running_jobs_resume_after_restart(monkeypatch, isolated_queue):
    incoming = isolated_queue / "restart.mp4"
    incoming.write_bytes(b"unfinished input")
    job = local_videos.enqueue(incoming, "restart.mp4")
    jobs = local_videos._jobs()
    jobs[job["id"]]["state"] = "running"
    local_videos._save(jobs)
    local_videos.init()
    assert local_videos.status()[0]["state"] == "queued"


def test_cannot_delete_running_job(isolated_queue):
    incoming = isolated_queue / "busy.mp4"
    incoming.write_bytes(b"busy")
    job = local_videos.enqueue(incoming, "busy.mp4")
    jobs = local_videos._jobs()
    jobs[job["id"]]["state"] = "running"
    local_videos._save(jobs)
    assert not local_videos.delete_job(job["id"])
    assert len(list(local_videos.INBOX.iterdir())) == 1
