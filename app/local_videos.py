"""Durable background queue for locally uploaded and preexisting videos.

Only normalized MP4 files enter the display playlist. Originals remain intact
until ffprobe + ffmpeg have produced and verified a complete compatible MP4.
"""
from __future__ import annotations

import os
import secrets
import shutil
import threading
import time
from pathlib import Path

from .storage import DATA, atomic_json, read_json
from .video_conversion import MAX_BYTES, normalize_video

VIDEO_EXTENSIONS = {".mp4", ".webm", ".m4v"}
PROFILE = "h264-1080p30-8bit-v1"
RESERVE_BYTES = 5 * 1024**3
INBOX = DATA / "local_video_inbox"
WORK = DATA / "local_video_work"
READY = DATA / "videos"
MARKERS = DATA / "local_video_ready"
JOBS = DATA / "local_video_jobs.json"
LOCK = threading.RLock()


def init() -> None:
    for folder in (INBOX, WORK, READY, MARKERS):
        folder.mkdir(parents=True, exist_ok=True)
    with LOCK:
        jobs = _jobs()
        changed = False
        for job in jobs.values():
            if job.get("state") == "running":
                job["state"] = "queued"
                changed = True
        if changed:
            _save(jobs)
    discover_legacy()


def _jobs() -> dict:
    return read_json(JOBS).get("jobs", {})


def _save(jobs: dict) -> None:
    atomic_json(JOBS, {"jobs": jobs})


def _source(job: dict) -> Path:
    folder = READY if job["location"] == "videos" else INBOX
    filename = job["source"]
    if Path(filename).name != filename:
        raise ValueError("Неверное имя исходного файла")
    return folder / filename


def _output(job: dict) -> Path:
    filename = job["output"]
    if Path(filename).name != filename or not filename.endswith(".mp4"):
        raise ValueError("Неверное имя итогового видео")
    return READY / filename


def is_ready(path: Path) -> bool:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".mp4":
        return False
    marker = MARKERS / (path.name + ".ready")
    try:
        return not marker.is_symlink() and marker.read_text(encoding="utf8") == PROFILE
    except (OSError, UnicodeError):
        return False


def mark_ready(path: Path) -> None:
    MARKERS.mkdir(parents=True, exist_ok=True)
    marker = MARKERS / (path.name + ".ready")
    temp = MARKERS / ("." + path.name + "." + secrets.token_hex(4) + ".tmp")
    try:
        temp.write_text(PROFILE, encoding="utf8")
        os.replace(temp, marker)
    finally:
        temp.unlink(missing_ok=True)


def remove_ready(path: Path) -> None:
    (MARKERS / (path.name + ".ready")).unlink(missing_ok=True)


def _entry(ident: str, source: Path, name: str, location: str) -> dict:
    stem = Path(name).stem[:76]
    return {
        "id": ident, "name": name, "source": source.name,
        "location": location, "output": f"{ident}-{stem}.mp4",
        "state": "queued", "error": "", "created": time.time(),
    }


def enqueue(incoming: Path, safe_name: str) -> dict:
    """Accept a completed upload, then persist its queue entry atomically."""
    suffix = Path(safe_name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS or Path(safe_name).name != safe_name:
        raise ValueError("Недопустимое имя видео")
    ident = secrets.token_hex(8)
    destination = INBOX / (ident + suffix)
    INBOX.mkdir(parents=True, exist_ok=True)
    os.replace(incoming, destination)
    entry = _entry(ident, destination, safe_name, "inbox")
    try:
        with LOCK:
            jobs = _jobs()
            jobs[ident] = entry
            _save(jobs)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"id": ident, "name": safe_name, "state": "queued"}


def discover_legacy() -> None:
    """Queue old manually uploaded files without deleting or modifying them."""
    READY.mkdir(parents=True, exist_ok=True)
    with LOCK:
        jobs = _jobs()
        occupied = {job["source"] for job in jobs.values()
                    if job.get("location") == "videos"}
        outputs = {job["output"] for job in jobs.values()}
        changed = False
        for path in sorted(READY.iterdir()):
            if (not path.is_file() or path.is_symlink()
                    or path.suffix.lower() not in VIDEO_EXTENSIONS
                    or path.name in occupied or path.name in outputs
                    or is_ready(path)):
                continue
            ident = secrets.token_hex(8)
            jobs[ident] = _entry(ident, path, path.name, "videos")
            occupied.add(path.name)
            changed = True
        if changed:
            _save(jobs)


def status() -> list[dict]:
    with LOCK:
        jobs = _jobs()
    return sorted(
        [{"id": job["id"], "name": job["name"], "state": job["state"],
          "error": job.get("error", ""), "origin": job["location"]}
         for job in jobs.values()],
        key=lambda job: job["name"],
    )


def retry(ident: str) -> bool:
    with LOCK:
        jobs = _jobs()
        job = jobs.get(ident)
        if not job or job["state"] != "failed" or not _source(job).is_file():
            return False
        job["state"] = "queued"
        job["error"] = ""
        _save(jobs)
        return True


def delete_job(ident: str) -> bool:
    with LOCK:
        jobs = _jobs()
        job = jobs.get(ident)
        if not job or job["state"] == "running":
            return False
        source = _source(job)
        source.unlink(missing_ok=True)
        del jobs[ident]
        _save(jobs)
        return True


def process_one() -> bool:
    """Handle at most one queued job. May be called again after a restart."""
    with LOCK:
        jobs = _jobs()
        job = next((j for j in jobs.values() if j.get("state") == "queued"), None)
        if job is None:
            return False
        job = dict(job)
        ident = job["id"]
        jobs[ident]["state"] = "running"
        _save(jobs)

    source = _source(job)
    output = _output(job)
    work = WORK / (ident + ".mp4")
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        if is_ready(output):
            # Previous process was interrupted after publishing the output.
            source.unlink(missing_ok=True)
        else:
            if not source.is_file() or source.is_symlink():
                raise ValueError("Исходный файл больше не существует")
            if shutil.disk_usage(DATA).free < RESERVE_BYTES + MAX_BYTES:
                raise ValueError("Недостаточно места для конвертации: требуется резерв 5 ГБ")
            work.unlink(missing_ok=True)
            print(f"[local-video] preparing {ident}: {job['name']}", flush=True)
            mode = normalize_video(source, work)
            if not source.is_file():
                raise ValueError("Исходный файл удалён до окончания обработки")
            remove_ready(output)
            os.replace(work, output)
            mark_ready(output)
            source.unlink(missing_ok=True)
            print(f"[local-video] ready {ident}: {output.name} ({mode})", flush=True)

        with LOCK:
            jobs = _jobs()
            jobs.pop(ident, None)
            _save(jobs)
    except Exception as exc:
        error = str(exc).strip()[:240] or exc.__class__.__name__
        with LOCK:
            jobs = _jobs()
            if ident in jobs:
                jobs[ident]["state"] = "failed"
                jobs[ident]["error"] = error
                _save(jobs)
        print(f"[local-video] failed {ident}: {error}", flush=True)
    finally:
        work.unlink(missing_ok=True)
    return True


def polling_loop(stop: threading.Event) -> None:
    init()
    while not stop.is_set():
        try:
            discover_legacy()
            processed = process_one()
        except Exception as exc:
            print(f"[local-video] worker error: {exc}", flush=True)
            processed = False
        if not processed and stop.wait(5):
            break
