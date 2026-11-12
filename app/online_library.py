"""Persistent human moderation of the online proposal catalog and ready videos.

The catalog lists proposed media; the selection journal determines what the
downloader may prepare and what the public screen may play. Historical items
retain metadata independently of downloaded files.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import online
from .storage import atomic_json, read_json

SELECTION_FILE = online.DATA / "online_selection.json"


def _valid_id(ident: str) -> bool:
    return isinstance(ident, str) and re.fullmatch(r"[0-9a-f]{24}", ident) is not None


def _entries() -> dict[str, dict]:
    raw = read_json(online.CATALOG_FILE).get("sources", {})
    found: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return found
    for provider, state in raw.items():
        if provider not in online.SOURCE_NAMES or not isinstance(state, dict):
            continue
        for entry in state.get("items", []):
            if not isinstance(entry, dict) or entry.get("provider") != provider:
                continue
            ident = entry.get("id")
            if _valid_id(ident) and online.allowed_video_url(
                    entry.get("remote_url"), provider):
                found[ident] = entry
    return found


def _metadata(entry: dict | None) -> dict:
    if not isinstance(entry, dict):
        return {}
    return {
        "name": str(entry.get("name") or "")[:110],
        "description": online._description(entry.get("description")),
        "name_en": str(entry.get("name_en") or "")[:110],
        "description_en": online._description(entry.get("description_en")),
        "provider": str(entry.get("provider") or "")[:30],
        "credit": str(entry.get("credit") or "")[:150],
        "page": str(entry.get("page") or "")[:400],
        "subject": online._description(entry.get("subject")),
        "year": str(entry.get("year") or "")[:12],
        "quality": str(entry.get("quality") or "")[:36],
        "quality_verified": entry.get("quality_verified") is True,
        "license": str(entry.get("license") or "")[:400],
        "thumbnail": str(entry.get("thumbnail") or "")[:400],
        "duration": entry.get("duration") if type(entry.get("duration")) is int else None,
    }


def _selection_state() -> set[str]:
    """Migrate existing ready MP4s ONCE; new catalog discoveries stay unchecked."""
    raw = read_json(SELECTION_FILE)
    if raw.get("version") == 1 and isinstance(raw.get("ids"), list):
        return {ident for ident in raw["ids"] if _valid_id(ident)}
    selected = set()
    for marker in online.CACHE_FOLDER.glob("*.ready"):
        if (not marker.is_symlink() and marker.is_file() and _valid_id(marker.stem)
                and (online.CACHE_FOLDER / (marker.stem + ".mp4")).is_file()):
            try:
                if marker.read_text(encoding="utf8") == online.CACHE_PROFILE:
                    selected.add(marker.stem)
            except OSError:
                continue
    atomic_json(SELECTION_FILE, {"version": 1, "ids": sorted(selected)})
    return selected


def selected_ids() -> set[str]:
    with online.LOCK:
        return _selection_state()


def is_selected(ident: str) -> bool:
    return _valid_id(ident) and ident in selected_ids()


def _one_shot_using(ident: str) -> bool:
    """Do not delete a file during a one-shot display command."""
    try:
        from . import main
        with main.LOCK:
            return any(
                source == "online" and name == ident
                and time.monotonic() - created < 15 * 60
                for created, source, name in main.DISPLAY_REQUESTS.values()
            )
    except (ImportError, AttributeError):
        return False


def cleanup_unselected() -> int:
    """Prune deselected ready files, but not work still running or one-shot media."""
    deleted = 0
    with online.LOCK:
        selected = _selection_state()
        for marker in online.CACHE_FOLDER.glob("*.ready"):
            ident = marker.stem
            if not _valid_id(ident) or ident in selected or ident in online.ACTIVE:
                continue
            if _one_shot_using(ident):
                continue
            file = online.CACHE_FOLDER / (ident + ".mp4")
            if file.is_symlink() or marker.is_symlink():
                continue
            file.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            deleted += 1
    return deleted


def set_selected(ids: list[str], selected: bool) -> dict:
    """Atomic batch moderation. No unknown IDs can be introduced via API."""
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or type(selected) is not bool:
        raise ValueError("Неверная команда выбора")
    if any(not _valid_id(ident) for ident in ids):
        raise ValueError("Неверный идентификатор ролика")
    with online.LOCK:
        entries = _entries()
        history = read_json(online.PROCESSED_FILE).get("details", {})
        if not isinstance(history, dict):
            history = {}
        known = set(entries) | set(history) | _selection_state()
        if any(ident not in known for ident in ids):
            raise ValueError("Ролик не найден в каталоге")
        if selected and any(ident not in entries for ident in ids):
            raise ValueError("Ролик больше не доступен в каталоге источника")
        current = _selection_state()
        before = len(current)
        if selected:
            current.update(ids)
            for ident in ids:
                online.CACHE_FAILED_UNTIL.pop(ident, None)
                online.CACHE_FAILED_REASONS.pop(ident, None)
        else:
            current.difference_update(ids)
            for ident in ids:
                event = online.CANCEL_EVENTS.get(ident)
                if event is not None:
                    event.set()
        atomic_json(SELECTION_FILE, {"version": 1, "ids": sorted(current)})
        if not selected:
            cleanup_unselected()
        return {"ok": True, "changed": abs(len(current) - before), "selected": len(current)}


def remember(ident: str, entry: dict | None = None) -> None:
    """Persist description and attribution across file deletion and refreshes."""
    if not _valid_id(ident):
        raise ValueError("Неверный идентификатор ролика")
    with online.LOCK:
        history = read_json(online.PROCESSED_FILE)
        done = online._processed_ids()
        done.add(ident)
        details = history.get("details", {})
        if not isinstance(details, dict):
            details = {}
        metadata = _metadata(entry)
        if metadata:
            older = details.get(ident, {})
            if isinstance(older, dict):
                for key, value in older.items():
                    if not metadata.get(key) and isinstance(value, str):
                        metadata[key] = value
            details[ident] = metadata
        atomic_json(online.PROCESSED_FILE, {
            "ids": sorted(done), "details": details,
        })


def list_videos() -> list[dict]:
    """Show EVERY discovered proposal, plus historical entries for orphaned files."""
    with online.LOCK:
        catalog = _entries()
        selected = _selection_state()
        history = read_json(online.PROCESSED_FILE)
        details = history.get("details", {})
        if not isinstance(details, dict):
            details = {}
        old = set(history.get("ids", []))
        known = set(catalog) | set(details) | {i for i in old if _valid_id(i)}
        known |= selected
        for marker in online.CACHE_FOLDER.glob("*.ready"):
            if _valid_id(marker.stem):
                known.add(marker.stem)
        items = []
        for ident in known:
            if not _valid_id(ident):
                continue
            file = online.cached_file(ident)
            metadata = _metadata(catalog.get(ident))
            previous = details.get(ident)
            if isinstance(previous, dict):
                for key, value in previous.items():
                    if not metadata.get(key) and value is not None:
                        metadata[key] = value
            active = ident in online.ACTIVE
            chosen = ident in selected
            items.append({
                "id": ident, **metadata,
                "name": metadata.get("name") or ident,
                "provider": metadata.get("provider") or "unknown",
                "bytes": file.stat().st_size if file else 0,
                "cached": file is not None,
                "selected": chosen,
                "active": active,
                "can_select": ident in catalog,
                "failed": online.CACHE_FAILED_UNTIL.get(ident, 0) > time.time(),
                "error": online.CACHE_FAILED_REASONS.get(ident, ""),
                "processed": ident in old,
            })
        return sorted(items, key=lambda item: (
            not item["cached"], not item["selected"],
            item["provider"], item["name"].casefold()
        ))


def ready_path(ident: str) -> Path | None:
    if not _valid_id(ident):
        return None
    with online.LOCK:
        return online.cached_file(ident)



def retry_selected(ident: str) -> bool:
    """Requeue a chosen, failed video without changing its moderation state."""
    if not _valid_id(ident):
        raise ValueError("Неверный идентификатор ролика")
    with online.LOCK:
        if ident not in _selection_state():
            raise ValueError("Сначала выберите ролик галочкой")
        if ident not in _entries():
            raise ValueError("Ролик отсутствует в каталоге источника")
        if ident in online.ACTIVE:
            raise ValueError("Ролик уже загружается")
        if online.cached_file(ident) is not None:
            raise ValueError("Ролик уже подготовлен")
        online.CACHE_FAILED_UNTIL.pop(ident, None)
        online.CACHE_FAILED_REASONS.pop(ident, None)
    return True
