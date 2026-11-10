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


def test_tls_certificate_verification_stays_enabled():
    import ssl
    ctx = online._tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname


def test_force_refresh_apple_but_not_commercial_api(monkeypatch, tmp_path):
    now = [1000000.0]
    monkeypatch.setattr(online.time, 'time', lambda: now[0])
    monkeypatch.setattr(online, 'CATALOG_FILE', tmp_path / 'catalog.json')
    calls = {'apple': 0, 'pixabay': 0}

    def make_fetch(provider):
        def fetch():
            calls[provider] += 1
            return [online._clip(
                provider, 'one', 'https://sylvan.apple.com/video.mp4'
                if provider == 'apple' else 'https://cdn.pixabay.com/video.mp4',
                'Test', 'Owner', 'https://example.org/'
            )]
        return fetch

    monkeypatch.setattr(online, 'FETCHERS', {
        'apple': make_fetch('apple'), 'pixabay': make_fetch('pixabay')
    })
    monkeypatch.setattr(online, 'settings', lambda: {
        'online_sources': {'apple': True, 'pixabay': True}
    })
    online.refresh_catalog()
    online.refresh_catalog(force=True)
    assert calls == {'apple': 1, 'pixabay': 1}
    now[0] += 61
    online.refresh_catalog(force=True)
    assert calls == {'apple': 2, 'pixabay': 1}


def test_tls_errors_are_reported_without_disabling_verification(monkeypatch):
    import ssl
    import urllib.error
    err = ssl.SSLCertVerificationError(
        1, 'certificate verify failed: unable to get local issuer certificate'
    )

    def fail(*args, **kwargs):
        assert kwargs.get('context').check_hostname
        raise urllib.error.URLError(err)

    monkeypatch.setattr(online.urllib.request, 'urlopen', fail)
    with pytest.raises(ValueError, match='HTTPS-сертификат'):
        online._http_json('https://sylvan.apple.com/Aerials/2x/entries.json')


def test_nasa_multiple_queries_and_mov_fallback(monkeypatch):
    urls = []

    def fake(url, **kw):
        urls.append(url)
        if "/search?" in url:
            if "earth+" in url or "earth&" in url:
                return {"collection": {"items": [{"data": [{"nasa_id": "no-mp4",
                                                              "title": "ISS Earth"}]}]}}
            return {"collection": {"items": []}}
        return {"collection": {"items": [
            {"href": "https://images-assets.nasa.gov/video/earth~orig.mov"}]}}
    monkeypatch.setattr(online, "_http_json", fake)
    clips = online._nasa()
    assert clips and clips[0]["remote_url"].endswith(".mov")
    assert any("/asset/no-mp4" in url for url in urls)


def test_range_validation_and_tls_only(monkeypatch):
    calls = []
    class FakeOpener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_header("Range")))
            return object()
    monkeypatch.setattr(online.urllib.request, "build_opener",
                        lambda *args: FakeOpener())
    entry = online._clip(
        "apple", "a", "https://sylvan.apple.com/Videos/ocean.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    online.open_video_stream(entry, "bytes=0-")
    assert calls[-1] == ("https://sylvan.apple.com/Videos/ocean.mov", "bytes=0-")
    with pytest.raises(ValueError):
        online.open_video_stream(entry, "bytes=0-10,30-40")
    with pytest.raises(ValueError):
        online.open_video_stream(entry, "bytes=abc")
    with pytest.raises(ValueError):
        online.open_video_stream({**entry, "remote_url": "http://127.0.0.1/private"})


def test_http_video_range_proxy_responds_as_mp4(monkeypatch):
    import io
    from fastapi.testclient import TestClient
    from app import main

    entry = online._clip(
        "apple", "a", "https://sylvan.apple.com/Videos/ocean.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    class Upstream(io.BytesIO):
        status = 206
        def __init__(self):
            super().__init__(b"test")
            self.headers = {
                "Content-Length": "4", "Content-Range": "bytes 0-3/100",
                "Content-Type": "video/quicktime"
            }

    seen = []
    monkeypatch.setattr(online, "lookup", lambda ident: entry)
    monkeypatch.setattr(online, "cached_file", lambda ident: None)
    monkeypatch.setattr(online, "try_cache", lambda entry: None)
    monkeypatch.setattr(
        online, "open_video_stream",
        lambda entry, byte_range: seen.append(byte_range) or Upstream()
    )
    client = TestClient(main.app)
    r = client.get("/media/online/" + entry["id"], headers={"Range": "bytes=0-3"})
    assert r.status_code == 206, r.text
    assert r.content == b"test"
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.headers["content-range"] == "bytes 0-3/100"
    assert r.headers["cache-control"] == "no-store"
    assert seen == ["bytes=0-3"]
