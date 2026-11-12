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
EXCLUDED_FILE = online.DATA / "online_excluded.json"
ARCHIVE_RESET_MARKER = online.DATA / "archive_catalog_reset_v1.json"


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



def _exclusion_key(entry: dict) -> str:
    """Identify Archive items across topics; other sources use clip IDs."""
    provider = entry.get("provider")
    page = str(entry.get("page") or "")
    if provider in online.ARCHIVE_THEMES:
        match = re.fullmatch(
            r"https://archive\.org/details/([A-Za-z0-9_.-]{1,160})", page)
        if match:
            return "archive:" + match.group(1)
    return str(entry.get("id") or "")


def _excluded_state() -> dict[str, dict]:
    raw = read_json(EXCLUDED_FILE).get("entries", {})
    return {key: value for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, dict)
            and _valid_id(value.get("id"))} if isinstance(raw, dict) else {}


def excluded_keys() -> set[str]:
    with online.LOCK:
        return set(_excluded_state())


def is_excluded(entry: dict) -> bool:
    with online.LOCK:
        return _exclusion_key(entry) in _excluded_state()


def list_excluded() -> list[dict]:
    with online.LOCK:
        return sorted(_excluded_state().values(),
                      key=lambda item: (str(item.get("provider")),
                                        str(item.get("name")).casefold()))


def _delete_cache_files(ident: str) -> bool:
    if ident in online.ACTIVE or _one_shot_using(ident):
        return False
    path = online.CACHE_FOLDER / (ident + ".mp4")
    marker = online.CACHE_FOLDER / (ident + ".ready")
    if path.is_symlink() or marker.is_symlink():
        return False
    path.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return True


def set_excluded(ident: str, excluded: bool) -> dict:
    """Persist user blacklist, cancel downloads and remove prepared media."""
    if not _valid_id(ident) or type(excluded) is not bool:
        raise ValueError("Неверный идентификатор или команда")
    with online.LOCK:
        current = _excluded_state()
        catalog = _entries()
        if excluded:
            entry = catalog.get(ident)
            history = read_json(online.PROCESSED_FILE)
            details = history.get("details", {})
            if not isinstance(details, dict):
                details = {}
            if entry is None:
                older = details.get(ident)
                if isinstance(older, dict):
                    entry = {"id": ident, **older}
            if entry is None:
                raise ValueError("Ролик не найден в каталоге")
            key = _exclusion_key(entry)
            if not key:
                raise ValueError("Не удалось определить источник")
            meta = {"id": ident, **_metadata(entry)}
            current[key] = meta
            related = {ident} | {
                video_id for video_id, candidate in catalog.items()
                if _exclusion_key(candidate) == key
            }
            selected = _selection_state()
            selected.difference_update(related)
            atomic_json(SELECTION_FILE, {"version": 1, "ids": sorted(selected)})
            # Keep a recovery record even if a provider later rotates its feed.
            details[ident] = _metadata(entry)
            history["details"] = details
            atomic_json(online.PROCESSED_FILE, history)
            for video_id in related:
                event = online.CANCEL_EVENTS.get(video_id)
                if event is not None:
                    event.set()
                online.CACHE_FAILED_REASONS.pop(video_id, None)
                online.CACHE_FAILED_UNTIL.pop(video_id, None)
            atomic_json(EXCLUDED_FILE, {"version": 1, "entries": current})
            for video_id in related:
                _delete_cache_files(video_id)
        else:
            match = next((key for key, record in current.items()
                          if record.get("id") == ident), None)
            if match is None:
                raise ValueError("Ролик не найден в исключениях")
            current.pop(match)
            atomic_json(EXCLUDED_FILE, {"version": 1, "entries": current})
        return {"ok": True, "excluded": len(current)}


def reset_archive_catalog_once() -> bool:
    """One-time startup reset, scoped strictly to Archive discoveries and cache."""
    with online.CATALOG_REFRESH_LOCK, online.LOCK:
        if ARCHIVE_RESET_MARKER.is_file():
            return False
        catalog = read_json(online.CATALOG_FILE)
        sources = catalog.get("sources", {})
        if not isinstance(sources, dict):
            sources = {}
        ids: set[str] = set()
        for provider in online.ARCHIVE_THEMES:
            state = sources.pop(provider, {})
            if isinstance(state, dict):
                for entry in state.get("items", []):
                    if isinstance(entry, dict) and _valid_id(entry.get("id")):
                        ids.add(entry["id"])
        history = read_json(online.PROCESSED_FILE)
        details = history.get("details", {})
        if not isinstance(details, dict):
            details = {}
        for ident, metadata in list(details.items()):
            if (isinstance(metadata, dict)
                    and metadata.get("provider") in online.ARCHIVE_THEMES):
                if _valid_id(ident):
                    ids.add(ident)
                details.pop(ident, None)
        # Do not touch Apple/NASA/Robin assets, user-uploaded files or settings.
        for ident in ids:
            for suffix in (".mp4", ".ready", ".part", ".mux.mp4"):
                path = online.CACHE_FOLDER / (ident + suffix)
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            online.CACHE_FAILED_UNTIL.pop(ident, None)
            online.CACHE_FAILED_REASONS.pop(ident, None)
        chosen = _selection_state() - ids
        atomic_json(SELECTION_FILE, {"version": 1, "ids": sorted(chosen)})
        history["ids"] = sorted(set(history.get("ids", [])) - ids)
        history["details"] = details
        atomic_json(online.PROCESSED_FILE, history)
        catalog["sources"] = sources
        atomic_json(online.CATALOG_FILE, catalog)
        atomic_json(ARCHIVE_RESET_MARKER, {
            "version": 1, "removed_archive_items": len(ids)})
        print(f"[online] one-time Archive cleanup: removed {len(ids)} old items",
              flush=True)
        return True

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
            if _delete_cache_files(ident):
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
        if selected and any(_exclusion_key(entries[ident]) in _excluded_state()
                            for ident in ids):
            raise ValueError("Ролик исключён из библиотеки")
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
        excluded = _excluded_state()
        for ident in known:
            if not _valid_id(ident):
                continue
            entry = catalog.get(ident) or details.get(ident) or {"id": ident}
            entry = {"id": ident, **entry}
            if _exclusion_key(entry) in excluded:
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
        # The same Archive recording can be discovered in multiple themes.
        # Prefer a selected/ready representative, suppress the other cards.
        unique = {}
        for item in items:
            key = _exclusion_key(item)
            prior = unique.get(key)
            if prior is None or ((item["selected"], item["cached"]) >
                                 (prior["selected"], prior["cached"])):
                unique[key] = item
        return sorted(unique.values(), key=lambda item: (
            not item["cached"], not item["selected"],
            item["provider"], item["name"].casefold()
        ))


def ready_path(ident: str) -> Path | None:
    if not _valid_id(ident):
        return None
    with online.LOCK:
        entry = _entries().get(ident)
        if entry is not None and _exclusion_key(entry) in _excluded_state():
            return None
        return online.cached_file(ident)



def retry_selected(ident: str) -> bool:
    """Requeue a chosen, failed video without changing its moderation state."""
    if not _valid_id(ident):
        raise ValueError("Неверный идентификатор ролика")
    with online.LOCK:
        if ident not in _selection_state():
            raise ValueError("Сначала выберите ролик галочкой")
        if _exclusion_key(_entries().get(ident, {})) in _excluded_state():
            raise ValueError("Ролик исключён")
        if ident not in _entries():
            raise ValueError("Ролик отсутствует в каталоге источника")
        if ident in online.ACTIVE:
            raise ValueError("Ролик уже загружается")
        if online.cached_file(ident) is not None:
            raise ValueError("Ролик уже подготовлен")
        online.CACHE_FAILED_UNTIL.pop(ident, None)
        online.CACHE_FAILED_REASONS.pop(ident, None)
    return True
