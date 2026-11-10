"""Prepared online library: metadata, no-redownload journal and admin safety."""
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


def test_catalog_description_preserved_and_shown_to_player(monkeypatch):
    monkeypatch.setattr(online, "_apple_manifest", lambda: {"assets": [{
        "id": "ocean", "description": "Aerial over the coast",
        "url-1080-SDR": "https://sylvan.apple.com/ocean.mov",
    }]})
    clip = online._apple()[0]
    assert clip["description"] == "Aerial over the coast"
    assert online._description("<p>Blue&nbsp;water</p>") == "Blue water"

    monkeypatch.setattr(online, "CATALOG_FILE", None)
    # Explicitly set the catalog for the playlist without performing a network call.
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder) / "catalog.json"
        monkeypatch.setattr(online, "CATALOG_FILE", p)
        atomic_json(p, {"sources": {"apple": {"items": [clip]}}})
        monkeypatch.setattr(online, "settings", lambda: {
            "online_sources": {"apple": True}, "online_cache_enabled": True
        })
        monkeypatch.setattr(online, "cached_file", lambda clip_id: p)
        assert online.list_clips()[0]["description"] == "Aerial over the coast"


def test_delete_keeps_history_and_allows_explicit_redownload(library):
    clip, output = library
    ident = clip["id"]
    online_library.remember(ident, clip)
    assert online_library.list_videos()[0]["description"] == "Beautiful waves in the morning"
    with pytest.raises(ValueError, match="Сначала удалите"):
        online_library.allow_redownload(ident)
    assert online_library.delete_video(ident)
    assert not output.exists()
    saved = read_json(online.PROCESSED_FILE)
    assert ident in saved["ids"]
    assert saved["details"][ident]["description"] == "Beautiful waves in the morning"
    missing = online_library.list_videos()
    assert len(missing) == 1
    assert not missing[0]["cached"]
    assert missing[0]["can_redownload"]
    assert missing[0]["description"] == "Beautiful waves in the morning"
    assert online_library.allow_redownload(ident)
    assert ident not in online._processed_ids()
    assert online_library.list_videos() == []


def test_unblock_rejects_unavailable_catalog(library):
    clip, output = library
    online_library.delete_video(clip["id"])
    atomic_json(online.CATALOG_FILE, {"sources": {}})
    with pytest.raises(ValueError, match="каталоге"):
        online_library.allow_redownload(clip["id"])


def test_deletion_rejects_running_or_invalid_id(library):
    clip, output = library
    assert not online_library.delete_video("../escape")
    assert online_library.ready_path("../escape") is None
    online.ACTIVE.add(clip["id"])
    try:
        with pytest.raises(ValueError, match="обрабатывается"):
            online_library.delete_video(clip["id"])
        assert output.exists()
    finally:
        online.ACTIVE.discard(clip["id"])


def test_admin_library_requires_login_and_csrf(library, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, auth

    clip, output = library
    auth.set_password("sample-library-password")
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    client = TestClient(main.app)
    ident = clip["id"]
    base = "/api/admin/online/library"
    assert client.get(base).status_code == 401
    assert client.get(base + "/" + ident + "/preview").status_code == 401
    assert client.delete(base + "/" + ident).status_code == 401
    result = client.post("/api/auth/login", json={"password": "sample-library-password"})
    assert result.status_code == 200
    csrf = {"x-csrf-token": result.json()["csrf"]}
    assert client.get(base).json()["videos"][0]["cached"]
    assert client.get(base + "/" + ident + "/preview").status_code == 200
    assert client.delete(base + "/" + ident).status_code == 403
    assert client.delete(base + "/" + ident, headers=csrf).status_code == 200
    assert client.post(base + "/" + ident + "/redownload", headers=csrf).status_code == 200
    assert client.get(base).json()["videos"] == []


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
