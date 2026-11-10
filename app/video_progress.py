"""In-memory telemetry for video downloads and conversion.

Progress is visible only through authenticated administration APIs. Never store
full media URLs, API keys or local paths in this module.
"""
from __future__ import annotations

import threading
import time

LOCK = threading.RLock()
TASKS: dict[tuple[str, str], dict] = {}
RECENT_TTL = 600
MAX_RECENT = 5


def _key(kind: str, ident: str) -> tuple[str, str]:
    if kind not in ("online", "local") or not isinstance(ident, str):
        raise ValueError("Invalid progress identity")
    return kind, ident


def start(kind: str, ident: str, name: str, provider: str = "") -> None:
    now = time.monotonic()
    with LOCK:
        TASKS[_key(kind, ident)] = {
            "kind": kind, "id": ident,
            "name": str(name)[:110], "provider": str(provider)[:32],
            "stage": "starting", "started": now, "updated": now,
            "bytes_received": 0, "bytes_total": None, "speed_bps": 0.0,
            "position_s": None, "duration_s": None, "encode_speed": None,
            "mode": None, "completed_at": None,
            "_sample_at": now, "_sample_bytes": 0, "_last_bytes_at": now,
        }


def download(kind: str, ident: str, received: int, total: int | None) -> None:
    now = time.monotonic()
    with LOCK:
        job = TASKS.get(_key(kind, ident))
        if not job:
            return
        received = max(0, int(received))
        total = int(total) if total is not None and int(total) > 0 else None
        if total is not None and received > total:
            total = None  # Server sent an incorrect Content-Length.
        sample_seconds = now - job["_sample_at"]
        if received > job["bytes_received"]:
            job["_last_bytes_at"] = now
        if sample_seconds >= 0.5:
            delta = max(0, received - job["_sample_bytes"])
            instant = delta / sample_seconds
            old = job["speed_bps"]
            job["speed_bps"] = instant if not old else 0.55 * instant + 0.45 * old
            job["_sample_at"] = now
            job["_sample_bytes"] = received
        job.update(stage="downloading", bytes_received=received,
                   bytes_total=total, updated=now)


def conversion(kind: str, ident: str, *, stage: str,
               mode: str | None = None, position_s: float | None = None,
               duration_s: float | None = None, encode_speed: str | None = None) -> None:
    if stage not in ("waiting", "converting", "finalizing"):
        raise ValueError("Invalid conversion stage")
    with LOCK:
        job = TASKS.get(_key(kind, ident))
        if not job:
            return
        job["stage"] = stage
        job["updated"] = time.monotonic()
        if mode is not None:
            job["mode"] = str(mode)[:20]
        if duration_s is not None:
            job["duration_s"] = max(0, float(duration_s))
        if position_s is not None:
            job["position_s"] = max(0, float(position_s))
        if encode_speed is not None:
            job["encode_speed"] = str(encode_speed)[:24]
        if stage != "downloading":
            job["speed_bps"] = 0.0


def finish(kind: str, ident: str, ok: bool, message: str = "") -> None:
    now = time.monotonic()
    with LOCK:
        job = TASKS.get(_key(kind, ident))
        if job:
            job.update(stage="ready" if ok else "failed", updated=now,
                       completed_at=now, message=str(message)[:120])


def _public(job: dict, now: float) -> dict:
    stage = job["stage"]
    percent = None
    if stage == "downloading" and job["bytes_total"]:
        percent = min(100.0, 100.0 * job["bytes_received"] / job["bytes_total"])
    elif stage == "converting" and job["duration_s"]:
        if job["position_s"] is not None:
            percent = min(99.9, max(0.0, 100.0 * job["position_s"] / job["duration_s"]))
    elif stage == "ready":
        percent = 100.0
    speed = job["speed_bps"] if now - job["_last_bytes_at"] < 5 else 0.0
    return {
        "kind": job["kind"], "id": job["id"], "name": job["name"],
        "provider": job["provider"], "stage": stage,
        "percent": round(percent, 1) if percent is not None else None,
        "bytes_received": job["bytes_received"],
        "bytes_total": job["bytes_total"],
        "speed_bps": round(speed),
        "position_s": job["position_s"],
        "duration_s": job["duration_s"],
        "encode_speed": job["encode_speed"], "mode": job["mode"],
        "elapsed_s": round(now - job["started"]),
        "message": job.get("message", ""),
    }


def snapshot() -> dict:
    now = time.monotonic()
    with LOCK:
        expired = [key for key, item in TASKS.items()
                   if item["completed_at"] is not None
                   and now - item["completed_at"] > RECENT_TTL]
        for key in expired:
            del TASKS[key]
        active = [_public(x, now) for x in TASKS.values()
                  if x["completed_at"] is None]
        latest = sorted(
            (x for x in TASKS.values() if x["completed_at"] is not None),
            key=lambda x: x["completed_at"], reverse=True,
        )[:MAX_RECENT]
        recent = [_public(x, now) for x in latest]
        return {"active": active, "recent": recent}
