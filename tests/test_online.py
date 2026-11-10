"""Online video providers: safe URLs, 24h API caching and offline fallback."""
from pathlib import Path

import pytest

from app import online
from app.storage import update_settings


def test_only_trusted_https_media_hosts():
    assert online.allowed_video_url("https://sylvan.apple.com/Aerials/a.mov", "apple")
    assert online.allowed_video_url("https://images-assets.nasa.gov/video/a.mp4", "nasa")
    assert online.allowed_video_url("https://cdn.pixabay.com/video/a.mp4", "pixabay")
    assert online.allowed_video_url("https://videos.pexels.com/video-files/a.mp4", "pexels")
    assert not online.allowed_video_url("http://sylvan.apple.com/a.mp4", "apple")
    assert not online.allowed_video_url("https://sylvan.apple.com.evil.test/a.mp4", "apple")
    assert not online.allowed_video_url("https://127.0.0.1/a.mp4", "nasa")
    assert not online.allowed_video_url("https://x:pw@sylvan.apple.com/a.mp4", "apple")
    assert not online.allowed_video_url("https://sylvan.apple.com:8080/a.mp4", "apple")
    assert not online.allowed_video_url("https://images-assets.nasa.gov/a.mp4", "apple")


def test_apple_catalog_selects_h264(monkeypatch):
    monkeypatch.setattr(online, "_http_json", lambda *args, **kw: [
        {"id": "a1", "name": "Ocean", "url-1080-H264":
            "https://sylvan.apple.com/Aerials/ocean.mov"},
        {"id": "bad", "url-1080-H264": "http://192.168.0.1/private"},
    ])
    entries = online._apple()
    assert len(entries) == 1
    assert entries[0]["provider"] == "apple"
    assert entries[0]["name"] == "Ocean"


def test_nasa_manifest_mp4(monkeypatch):
    def fake(url, **kw):
        if "/search?" in url:
            return {"collection": {"items": [
                {"data": [{"nasa_id": "earth1", "title": "Earth"}]}]}}
        return {"collection": {"items": [
            {"href": "https://images-assets.nasa.gov/video/earth1.mp4"}]}}
    monkeypatch.setattr(online, "_http_json", fake)
    entries = online._nasa()
    assert len(entries) == 1 and entries[0]["provider"] == "nasa"


def test_pixabay_and_pexels_need_keys(monkeypatch):
    monkeypatch.setattr(online, "_key", lambda name: "")
    with pytest.raises(ValueError):
        online._pixabay()
    with pytest.raises(ValueError):
        online._pexels()


def test_pixabay_catalog_uses_medium_mp4(monkeypatch):
    monkeypatch.setattr(online, "_key", lambda name: "fake-test-key")
    def fake(url, **kw):
        assert "api/videos" in url
        return {"hits": [{"id": 123, "user": "Author",
            "pageURL": "https://pixabay.com/videos/test-123/",
            "videos": {"medium": {"url": "https://cdn.pixabay.com/video/test.mp4"}}}]}
    monkeypatch.setattr(online, "_http_json", fake)
    assert online._pixabay()[0]["provider"] == "pixabay"


def test_pexels_catalog_parsing(monkeypatch):
    monkeypatch.setattr(online, "_key", lambda name: "fake-test-key")
    def fake(url, **kw):
        assert kw["headers"]["Authorization"] == "fake-test-key"
        return {"videos": [{"id": 123, "user": {"name": "Jane"},
            "url": "https://www.pexels.com/video/test-123/",
            "video_files": [{"file_type": "video/mp4", "width": 1920, "height": 1080,
                             "link": "https://videos.pexels.com/video-files/test.mp4"}]}]}
    monkeypatch.setattr(online, "_http_json", fake)
    assert online._pexels()[0]["credit"] == "Pexels / Jane"


def test_catalog_respects_metadata_ttl(monkeypatch, tmp_path):
    path = tmp_path / "catalog.json"
    calls = []
    clip = online._clip("apple", "ocean", "https://sylvan.apple.com/ocean.mp4",
                        "Ocean", "Apple Aerial", "https://www.apple.com/tv/")
    monkeypatch.setattr(online, "CATALOG_FILE", path)
    monkeypatch.setattr(online, "FETCHERS", {"apple": lambda: calls.append(1) or [clip]})
    monkeypatch.setattr(online, "settings", lambda: {"online_sources": {"apple": True}})
    online.refresh_catalog()
    online.refresh_catalog()
    assert len(calls) == 1
    assert len(online.list_clips()) == 1


def test_disabled_provider_never_surfaces(monkeypatch, tmp_path):
    clip = online._clip("apple", "ocean", "https://sylvan.apple.com/ocean.mp4",
                        "Ocean", "Apple", "https://www.apple.com/tv/")
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    online.atomic_json(online.CATALOG_FILE,
                       {"sources": {"apple": {"items": [clip], "updated": 10}}})
    monkeypatch.setattr(online, "settings", lambda: {"online_sources": {"apple": False}})
    assert online.list_clips() == []
    assert online.lookup(clip["id"]) is None


def test_source_settings_validation():
    valid = {"apple": True, "nasa": True, "pixabay": False, "pexels": False}
    update_settings({"online_sources": valid, "online_cache_gb": 12})
    with pytest.raises(ValueError):
        update_settings({"online_sources": {"apple": True}})
    with pytest.raises(ValueError):
        update_settings({"online_cache_gb": 1000})
