"""Drain media work before invoking the separate release installer."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from .storage import DATA, LOCK, atomic_json, read_json

_GATE = threading.RLock()
_active = 0


def paused() -> bool:
    return ((DATA / "update_request.json").exists()
            or (DATA / "pending_update.json").exists())


@contextmanager
def media_job():
    """Admission and idle observation share a lock; admitted jobs always finish."""
    global _active
    with _GATE:
        admitted = not paused()
        if admitted:
            _active += 1
    try:
        yield admitted
    finally:
        if admitted:
            with _GATE:
                _active -= 1


def ready() -> bool:
    with _GATE:
        return paused() and _active == 0


def request(payload: dict, automatic: bool = False) -> None:
    with LOCK:
        if paused():
            raise ValueError("Предыдущее обновление ещё выполняется")
        atomic_json(DATA / "update_request.json", {
            "payload": payload, "automatic": automatic, "requested_at": time.time(),
            "previous_status": read_json(DATA / "update_status.json"),
        })
        promote()


def promote() -> None:
    """Long conversions drain in the app, outside systemd's installer timeout."""
    with LOCK:
        if (DATA / "pending_update.json").exists():
            return
        queued = read_json(DATA / "update_request.json")
        if not queued:
            return
        # Also recover requests consumed by an older installed helper.
        status = read_json(DATA / "update_status.json")
        if status and status != queued.get("previous_status", status):
            (DATA / "update_request.json").unlink(missing_ok=True)
            return
        if ready():
            payload = dict(queued["payload"])
            if queued.get("automatic") is True:
                payload["_automatic"] = True
            atomic_json(DATA / "pending_update.json", payload)
