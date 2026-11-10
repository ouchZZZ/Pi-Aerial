"""Authenticated management of prepared online videos and their re-download history."""
from __future__ import annotations

import re
from pathlib import Path

from . import online
from .storage import atomic_json, read_json


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
        "provider": str(entry.get("provider") or "")[:24],
        "credit": str(entry.get("credit") or "")[:150],
        "page": str(entry.get("page") or "")[:400],
    }


def remember(ident: str, entry: dict | None = None) -> None:
    """Persist attribution and description independently of cache eviction."""
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
    """Include prepared MP4s and deleted clips still blocked from downloading."""
    with online.LOCK:
        catalog = _entries()
        done = online._processed_ids()
        history = read_json(online.PROCESSED_FILE)
        details = history.get("details", {})
        if not isinstance(details, dict):
            details = {}
        ids = set(done)
        for marker in online.CACHE_FOLDER.glob("*.ready"):
            if _valid_id(marker.stem) and marker.name.endswith(".ready"):
                ids.add(marker.stem)
        items = []
        for ident in ids:
            file = online.cached_file(ident)
            metadata = _metadata(catalog.get(ident))
            older = details.get(ident, {})
            if isinstance(older, dict):
                for key, value in older.items():
                    if not metadata.get(key) and isinstance(value, str):
                        metadata[key] = value
            items.append({
                "id": ident,
                "name": metadata.get("name") or ident,
                "description": metadata.get("description") or "",
                "provider": metadata.get("provider") or "unknown",
                "credit": metadata.get("credit") or "",
                "page": metadata.get("page") or "",
                "bytes": file.stat().st_size if file else 0,
                "cached": file is not None,
                "processed": ident in done,
                "can_redownload": ident in catalog,
                "active": ident in online.ACTIVE,
            })
        return sorted(items, key=lambda item: (
            not item["cached"], item["provider"], item["name"]
        ))


def ready_path(ident: str) -> Path | None:
    if not _valid_id(ident):
        return None
    with online.LOCK:
        return online.cached_file(ident)


def delete_video(ident: str) -> bool:
    """Remove the MP4 but retain its processed history and description."""
    if not _valid_id(ident):
        return False
    with online.LOCK:
        if ident in online.ACTIVE:
            raise ValueError("Ролик ещё обрабатывается")
        file = online.cached_file(ident)
        if file is None:
            return False
        remember(ident, _entries().get(ident))
        file.unlink()
        file.with_suffix(".ready").unlink(missing_ok=True)
        return True


def allow_redownload(ident: str) -> bool:
    """Only removed, previously processed files may be unblocked explicitly."""
    if not _valid_id(ident):
        return False
    with online.LOCK:
        if ident in online.ACTIVE or online.cached_file(ident) is not None:
            raise ValueError("Сначала удалите видео или дождитесь его обработки")
        if ident not in _entries():
            raise ValueError("Ролика больше нет в актуальном каталоге источника")
        done = online._processed_ids()
        if ident not in done:
            return False
        done.remove(ident)
        history = read_json(online.PROCESSED_FILE)
        details = history.get("details", {})
        if not isinstance(details, dict):
            details = {}
        details.pop(ident, None)
        atomic_json(online.PROCESSED_FILE, {
            "ids": sorted(done), "details": details,
        })
        online.CACHE_FAILED_UNTIL.pop(ident, None)
        return True
