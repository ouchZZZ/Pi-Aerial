"""Moderated online library: safe migrations, explicit queueing and admin safety."""
from __future__ import annotations

import pytest

from app import online, online_library
from app.storage import atomic_json, read_json


@pytest.fixture
def library(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
    monkeypatch.setattr(online_library, "SELECTION_FILE", tmp_path / "selection.json")
    clip = online._clip(
        "apple", "sample", "https://sylvan.apple.com/video.mov",
        "Ocean", "Apple Aerial", "https://www.apple.com/tv/",
        "Beautiful waves in the morning",
    )
    atomic_json(online.CATALOG_FILE, {
        "sources": {"apple": {"items": [clip]}}
    })
    output = cache / (clip["id"] + ".mp4")
    output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"0" * 2048)
    output.with_suffix(".ready").write_text(online.CACHE_PROFILE)
    return clip, output


def test_catalog_description_preserved_and_shown_to_player(monkeypatch, tmp_path):
    monkeypatch.setattr(online, "_apple_manifest", lambda: {"assets": [{
        "id": "ocean", "description": "Aerial over the coast",
        "url-1080-SDR": "https://sylvan.apple.com/ocean.mov",
    }]})
    clip = online._apple()[0]
    assert clip["description"] == "Aerial over the coast"
    assert online._description("<p>Blue&nbsp;water</p>") == "Blue water"

    path = tmp_path / "catalog.json"
    monkeypatch.setattr(online, "CATALOG_FILE", path)
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path / "cache")
    atomic_json(path, {"sources": {"apple": {"items": [clip]}}})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": True}, "online_cache_enabled": True
    })
    monkeypatch.setattr(online, "cached_file", lambda clip_id: path)
    assert online.list_clips() == []  # Not selected yet.
    online_library.set_selected([clip["id"]], True)
    assert online.list_clips()[0]["description"] == "Aerial over the coast"


def test_first_migration_selects_ready_files_only(library, monkeypatch):
    clip, output = library
    ident = clip["id"]
    # The previously installed cached file must survive the new policy.
    assert online_library.selected_ids() == {ident}
    assert read_json(online_library.SELECTION_FILE) == {
        "version": 1, "ids": [ident]
    }
    assert online_library.list_videos()[0]["selected"]
    online_library.remember(ident, clip)
    # User removes the check. History remains, file is deleted.
    result = online_library.set_selected([ident], False)
    assert result["ok"]
    assert not output.exists()
    assert not online_library.is_selected(ident)
    assert read_json(online.PROCESSED_FILE)["details"][ident]["description"] == (
        "Beautiful waves in the morning")
    assert len(online_library.list_videos()) == 1
    # Re-select with old processed ID and download can occur again.
    online_library.set_selected([ident], True)
    assert online_library.is_selected(ident)
    assert not output.exists()


def test_new_catalog_items_start_unselected(library):
    clip, output = library
    other = online._clip("apple", "new", "https://sylvan.apple.com/new.mov",
                         "New", "Apple", "https://www.apple.com/tv/")
    atomic_json(online.CATALOG_FILE, {"sources": {
        "apple": {"items": [clip, other]}
    }})
    listed = online_library.list_videos()
    assert len(listed) == 2
    assert next(v for v in listed if v["id"] == other["id"])["selected"] is False
    assert online_library.selected_ids() == {clip["id"]}


def test_only_catalog_known_ids_can_be_selected(library):
    clip, _ = library
    with pytest.raises(ValueError, match="не найден"):
        online_library.set_selected(["f" * 24], True)
    with pytest.raises(ValueError, match="идентификатор"):
        online_library.set_selected(["../../../etc/passwd"], False)
    with pytest.raises(ValueError, match="Неверная команда"):
        online_library.set_selected([], True)


def test_deselection_cancels_running_download(library):
    import threading
    clip, output = library
    ident = clip["id"]
    event = threading.Event()
    online.CANCEL_EVENTS[ident] = event
    online.ACTIVE.add(ident)
    try:
        online_library.set_selected([ident], False)
        assert event.is_set()
        assert output.exists()  # Active worker owns the file and will clean it.
    finally:
        online.ACTIVE.discard(ident)
        online.CANCEL_EVENTS.pop(ident, None)
    online_library.cleanup_unselected()
    assert not output.exists()


def test_admin_moderation_requires_login_and_csrf(library, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, auth
    clip, output = library
    auth.set_password("sample-library-password")
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    client = TestClient(main.app)
    ident = clip["id"]
    base = "/api/admin/online/library"
    payload = {"ids": [ident], "selected": False}
    assert client.get(base).status_code == 401
    assert client.get(base + "/" + ident + "/preview").status_code == 401
    assert client.post(base + "/selection", json=payload).status_code == 401
    result = client.post("/api/auth/login", json={"password": "sample-library-password"})
    assert result.status_code == 200
    csrf = {"x-csrf-token": result.json()["csrf"]}
    assert client.get(base).json()["videos"][0]["cached"]
    assert client.get(base + "/" + ident + "/preview").status_code == 200
    assert client.post(base + "/selection", json=payload).status_code == 403
    assert client.post(base + "/selection", json=payload, headers=csrf).status_code == 200
    assert client.get(base).json()["videos"][0]["selected"] is False
    assert not output.exists()
    assert client.post(base + "/selection", json={"ids": ["bad"], "selected": True},
                       headers=csrf).status_code == 422
    assert client.put(base + "/" + ident + "/selection",
                      json={"ids": [ident], "selected": True}, headers=csrf).status_code == 200
    assert client.get(base).json()["videos"][0]["selected"] is True


def test_one_shot_accepts_both_ready_sources(library, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, auth, local_videos

    clip, output = library
    monkeypatch.setattr(main, "DISPLAY_REQUESTS", {})
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(local_videos, "MARKERS", tmp_path / "markers")
    local = tmp_path / "videos" / "Мой ролик.mp4"
    local.parent.mkdir()
    local.write_bytes(b"local MP4")
    local_videos.mark_ready(local)
    auth.set_password("sample-library-password")

    admin = TestClient(main.app)
    screen = TestClient(main.app)
    poll = "/api/display/play-once"
    command = "/api/admin/display/play-once"
    assert screen.get(poll).json() == {"stop": True}
    assert screen.post(f"{command}/online/{clip['id']}").status_code == 401
    assert screen.post(f"{command}/stop").status_code == 401
    result = admin.post("/api/auth/login", json={"password": "sample-library-password"})
    csrf = {"x-csrf-token": result.json()["csrf"]}
    assert admin.post(f"{command}/local/{local.name}").status_code == 403
    assert admin.post(f"{command}/local/missing.mp4", headers=csrf).status_code == 404

    for source, name, file in [("online", clip["id"], output), ("local", local.name, local)]:
        assert admin.post(f"{command}/{source}/{name}", headers=csrf).status_code == 200
        request = screen.get(poll).json()
        response = screen.get(request["url"], headers={"range": "bytes=0-3"})
        assert response.status_code == 206
        assert response.content == file.read_bytes()[:4]
        assert response.headers["cache-control"] == "no-store"

    assert screen.get(poll + "/unknown").status_code == 404
    token = request["id"]
    created, source, name = main.DISPLAY_REQUESTS[token]
    main.DISPLAY_REQUESTS[token] = (created - main.DISPLAY_REQUEST_WINDOW, source, name)
    assert screen.get(poll).json() is None
    assert screen.get(request["url"]).content == local.read_bytes()

    assert admin.post(f"{command}/stop").status_code == 403
    assert admin.post(f"{command}/stop", headers=csrf).status_code == 200
    assert screen.get(poll).json() == {"stop": True}
    assert screen.get(request["url"]).status_code == 404
    assert admin.post(f"{command}/local/{local.name}", headers=csrf).status_code == 200
    assert screen.get(poll).json()["id"] != token


def test_public_release_has_attribution_but_no_download_button():
    from pathlib import Path
    web = Path(__file__).parents[1] / 'web'
    assert 'libraryPreviewDownload' not in (web / 'admin.html').read_text()
    assert 'libraryPreviewDownload' not in (web / 'admin.js').read_text()
    screen = (web / 'display.html').read_text()
    assert 'https://open-meteo.com/' in screen
    assert 'https://creativecommons.org/licenses/by/4.0/' in screen



def test_retry_selected_failed_video_is_authenticated_and_clears_cooldown(
        library, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from app import main, auth

    clip, output = library
    ident = clip["id"]
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    monkeypatch.setattr(online, "CACHE_FAILED_UNTIL", {})
    monkeypatch.setattr(online, "CACHE_FAILED_REASONS", {})
    online_library.set_selected([ident], False)
    assert not output.exists()

    auth.set_password("sample-retry-password")
    client = TestClient(main.app)
    uri = "/api/admin/online/library/" + ident + "/retry"
    assert client.post(uri).status_code == 401
    token = client.post("/api/auth/login",
                        json={"password": "sample-retry-password"}).json()["csrf"]
    assert client.post(uri).status_code == 403
    assert client.post(uri, headers={"x-csrf-token": token}).status_code == 409

    online_library.set_selected([ident], True)
    online.CACHE_FAILED_UNTIL[ident] = time.time() + 3600
    online.CACHE_FAILED_REASONS[ident] = "HTTP 503: сервер временно недоступен"
    item = next(i for i in online_library.list_videos() if i["id"] == ident)
    assert item["failed"] is True
    assert "HTTP 503" in item["error"]
    assert client.post(uri, headers={"x-csrf-token": token}).status_code == 200
    assert ident not in online.CACHE_FAILED_UNTIL
    assert ident not in online.CACHE_FAILED_REASONS
    assert client.post(uri, headers={"x-csrf-token": token}).status_code == 200
    assert client.post("/api/admin/online/library/bad-id/retry",
                       headers={"x-csrf-token": token}).status_code == 409
