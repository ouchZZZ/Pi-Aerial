"""Online video providers: tvOS 26, safe URLs and no-redownload history."""
from pathlib import Path

import pytest

from app import online
from app.storage import update_settings


def test_only_trusted_https_media_hosts():
    assert online.allowed_video_url("https://sylvan.apple.com/Aerials/a.mov", "apple")
    assert online.allowed_video_url("https://images-assets.nasa.gov/video/a.mp4", "nasa")
    assert not online.allowed_video_url("https://cdn.pixabay.com/video/a.mp4", "pixabay")
    assert not online.allowed_video_url("https://videos.pexels.com/video-files/a.mp4", "pexels")
    assert not online.allowed_video_url("http://sylvan.apple.com/a.mp4", "apple")
    assert not online.allowed_video_url("https://sylvan.apple.com.evil.test/a.mp4", "apple")
    assert not online.allowed_video_url("https://127.0.0.1/a.mp4", "nasa")
    assert not online.allowed_video_url("https://x:pw@sylvan.apple.com/a.mp4", "apple")
    assert not online.allowed_video_url("https://sylvan.apple.com:8080/a.mp4", "apple")
    assert not online.allowed_video_url("https://images-assets.nasa.gov/a.mp4", "apple")
    assert online.nasa_https_url("http://images-assets.nasa.gov/video/earth~orig.mp4") == "https://images-assets.nasa.gov/video/earth~orig.mp4"
    assert online.nasa_https_url("http://169.254.169.254/latest/meta-data") is None
    assert online.nasa_https_url("http://nasa.gov.evil.test/clip.mp4") is None
    assert online.nasa_https_url("http://images-assets.nasa.gov/video/file name.mp4") == "https://images-assets.nasa.gov/video/file%20name.mp4"
    assert online.nasa_https_url("https://images-assets.nasa.gov/video/earth.mov") == "https://images-assets.nasa.gov/video/earth.mov"


def test_apple_catalog_selects_h264(monkeypatch):
    monkeypatch.setattr(online, "_apple_manifest", lambda: [
        {"id": "a1", "name": "Ocean", "url-1080-H264":
            "https://sylvan.apple.com/Aerials/ocean.mov",
         "url-4K-SDR": "https://sylvan.apple.com/Aerials/ocean4k.mov"},
        {"id": "bad", "url-1080-H264": "http://192.168.0.1/private"},
    ])
    entries = online._apple()
    assert len(entries) == 1
    assert entries[0]["provider"] == "apple"
    assert entries[0]["name"] == "Ocean"
    assert entries[0]["remote_url"].endswith("/ocean.mov")


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


def test_apple_catalog_prefers_1080_hevc_over_4k(monkeypatch):
    monkeypatch.setattr(online, "_apple_manifest", lambda: {
        "assets": [{"id": "ocean", "url-1080-SDR":
                    "https://sylvan.apple.com/1080.mov",
                    "url-4K-SDR": "https://sylvan.apple.com/4k.mov"}]})
    assert online._apple()[0]["remote_url"].endswith("/1080.mov")


def test_tvos26_tar_manifest_parsed_without_extraction(monkeypatch):
    import io
    import json
    import tarfile

    data = json.dumps({"assets": [{"id": "new-clip"}]}).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("Customer/entries.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    class FakeResponse(io.BytesIO):
        pass

    monkeypatch.setattr(online.urllib.request, "urlopen",
                        lambda *args, **kwargs: FakeResponse(stream.getvalue()))
    assert online._apple_manifest()["assets"][0]["id"] == "new-clip"


def test_catalog_respects_metadata_ttl(monkeypatch, tmp_path):
    path = tmp_path / "catalog.json"
    calls = []
    clip = online._clip("apple", "ocean", "https://sylvan.apple.com/ocean.mp4",
                        "Ocean", "Apple Aerial", "https://www.apple.com/tv/")
    monkeypatch.setattr(online, "CATALOG_FILE", path)
    monkeypatch.setattr(online, "FETCHERS", {"apple": lambda: calls.append(1) or [clip]})
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    monkeypatch.setattr(online, "settings", lambda: {"online_sources": {"apple": True}})
    online.refresh_catalog()
    online.refresh_catalog()
    assert len(calls) == 1
    # Metadata may be ready before the background MP4 conversion finishes.
    assert len(online.list_clips()) == 0
    assert online.source_status()[0]["count"] == 1


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
    valid = {"apple": True, "nasa": True}
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


def test_force_refresh_apple_and_nasa_after_one_minute(monkeypatch, tmp_path):
    now = [1000000.0]
    monkeypatch.setattr(online.time, "time", lambda: now[0])
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    calls = {"apple": 0, "nasa": 0}

    def make_fetch(provider):
        def fetch():
            calls[provider] += 1
            host = "sylvan.apple.com" if provider == "apple" else "images-assets.nasa.gov"
            return [online._clip(
                provider, "one", "https://" + host + "/video.mp4",
                "Test", "Owner", "https://example.org/"
            )]
        return fetch

    monkeypatch.setattr(online, "FETCHERS", {
        "apple": make_fetch("apple"), "nasa": make_fetch("nasa")
    })
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": True, "nasa": True}
    })
    online.refresh_catalog()
    online.refresh_catalog(force=True)
    assert calls == {"apple": 1, "nasa": 1}
    now[0] += 61
    online.refresh_catalog(force=True)
    assert calls == {"apple": 2, "nasa": 2}


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
        "nasa", "a", "https://images-assets.nasa.gov/video/earth.mp4",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    class Upstream(io.BytesIO):
        status = 206
        def __init__(self):
            super().__init__(b"test")
            self.headers = {
                "Content-Length": "4", "Content-Range": "bytes 0-3/100",
                "Content-Type": "video/mp4"
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


def test_old_quicktime_cache_is_invalidated(monkeypatch, tmp_path):
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    ident = "a" * 24
    path = tmp_path / (ident + ".mp4")
    path.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"X" * 1500)
    assert online.cached_file(ident) is None
    assert not path.exists()


def test_apple_mov_cache_forces_transcode_and_removes_source(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_cache_enabled": True, "online_cache_gb": 12
    })
    disk = SimpleNamespace(free=100 * 1024 ** 3, total=128 * 1024 ** 3)
    monkeypatch.setattr(online.shutil, "disk_usage", lambda folder: disk)
    monkeypatch.setattr(online.shutil, "which", lambda command: "/usr/bin/ffmpeg")
    class FakeResponse(io.BytesIO):
        def __init__(self):
            super().__init__(b"\x00\x00\x00\x18ftypqt  " + b"z" * 2500)
            self.headers = {"Content-Length": "2512"}
    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()
    monkeypatch.setattr(online.urllib.request, "build_opener",
                        lambda *args: FakeOpener())
    recorded = []
    def fake_normalize(source, output, on_progress=None, force_transcode=False):
        assert force_transcode is True
        recorded.append(str(source))
        output.write_bytes(bytes.fromhex("000000186674797069736f6d") + b"M" * 2500)
        return "transcode"
    monkeypatch.setattr(online, "normalize_video", fake_normalize)
    clip = online._clip(
        "apple", "ocean", "https://sylvan.apple.com/Videos/ocean.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    assert online.DOWNLOAD_SLOT.acquire(blocking=False)
    online._cache_worker(clip)
    path = tmp_path / (clip["id"] + ".mp4")
    assert path.is_file()
    assert path.read_bytes()[8:12] == b"isom"
    assert (tmp_path / (clip["id"] + ".ready")).read_text() == online.CACHE_PROFILE
    assert recorded
    assert not (tmp_path / (clip["id"] + ".part")).exists()
    assert clip["id"] in online._processed_ids()


def test_nasa_http_manifest_links_are_upgraded(monkeypatch):
    def fake(url, **kw):
        if "/search?" in url:
            return {"collection": {"items": [{"data": [{"nasa_id": "earth",
                                                          "title": "Earth"}]}]}}
        return {"collection": {"items": [{
            "href": "http://images-assets.nasa.gov/video/earth/earth~orig.mp4"}]}}
    monkeypatch.setattr(online, "_http_json", fake)
    clips = online._nasa()
    assert clips and clips[0]["remote_url"] == (
        "https://images-assets.nasa.gov/video/earth/earth~orig.mp4"
    )


def test_mov_not_advertised_until_real_mp4_cache_ready(monkeypatch, tmp_path):
    clip = online._clip(
        "apple", "ocean", "https://sylvan.apple.com/Videos/ocean.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path / "cache")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": True, "nasa": False},
        "online_cache_enabled": True,
    })
    online.CACHE_FOLDER.mkdir(parents=True)
    online.atomic_json(online.CATALOG_FILE,
                       {"sources": {"apple": {"items": [clip], "updated": 1}}})
    assert online.list_clips() == []
    from pathlib import Path
    (online.CACHE_FOLDER / (clip["id"] + ".mp4")).write_bytes(
        bytes.fromhex("000000186674797069736f6d") + b"0" * 1500
    )
    (online.CACHE_FOLDER / (clip["id"] + ".ready")).write_text(online.CACHE_PROFILE)
    clips = online.list_clips()
    assert len(clips) == 1 and clips[0]["cached"] is True
    assert online.source_status()[0]["playable"] == 1


def test_only_bounded_mov_prefetch_one_at_a_time(monkeypatch, tmp_path):
    clips = [online._clip(
        "apple", str(n), f"https://sylvan.apple.com/Videos/{n}.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    ) for n in range(7)]
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path / "cache")
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    online.atomic_json(online.CATALOG_FILE,
                       {"sources": {"apple": {"items": clips, "updated": 1}}})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": True, "nasa": False},
        "online_cache_enabled": True,
    })
    monkeypatch.setattr(online.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    calls = []
    monkeypatch.setattr(online, "try_cache", lambda entry: calls.append(entry["id"]))
    online.warm_cache()
    assert calls == [clips[0]["id"]]


def test_processed_history_prevents_repeated_download_after_eviction(monkeypatch, tmp_path):
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path / "cache")
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    online.CACHE_FOLDER.mkdir()
    clip = online._clip("apple", "already-done",
                        "https://sylvan.apple.com/ready.mov",
                        "Done", "Apple", "https://www.apple.com/")
    online._mark_processed(clip["id"])
    assert clip["id"] in online._processed_ids()
    monkeypatch.setattr(online, "settings", lambda: {
        "online_cache_enabled": True,
        "online_sources": {"apple": True, "nasa": False},
    })
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {"apple": {"items": [clip]}}
    })
    monkeypatch.setattr(online.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    online.warm_cache()
    assert clip["id"] not in online.ACTIVE


def test_cache_limit_pauses_without_eviction_then_resumes(monkeypatch, tmp_path):
    from types import SimpleNamespace

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    config = {"online_sources": {"apple": True, "nasa": False},
              "online_cache_enabled": True, "online_cache_gb": 1}
    monkeypatch.setattr(online, "settings", lambda: config)
    monkeypatch.setattr(
        online.shutil, "disk_usage",
        lambda path: SimpleNamespace(total=128 * 1024**3, free=30 * 1024**3),
    )
    monkeypatch.setattr(online.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    old = online._clip("apple", "old", "https://sylvan.apple.com/old.mov",
                       "Old", "Apple", "https://www.apple.com/")
    new = online._clip("apple", "new", "https://sylvan.apple.com/new.mov",
                       "New", "Apple", "https://www.apple.com/")
    ready = cache / (old["id"] + ".mp4")
    with ready.open("wb") as video:
        video.truncate(800 * 1024**2)
    (cache / (old["id"] + ".ready")).write_text(online.CACHE_PROFILE)
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {"apple": {"items": [old, new]}}
    })

    status = online.cache_status()
    assert status["paused"] and status["pause_reason"] == "limit"
    assert status["used_bytes"] == 800 * 1024**2
    calls = []
    monkeypatch.setattr(online, "try_cache", lambda entry: calls.append(entry["id"]))
    online.warm_cache()
    assert calls == []
    assert ready.exists()  # no eviction, even at the configured limit

    config["online_cache_gb"] = 2
    assert not online.cache_status()["paused"]
    online.warm_cache()
    assert calls == [new["id"]]
    assert ready.exists()


def test_cache_pauses_if_disk_reserve_is_threatened(monkeypatch, tmp_path):
    from types import SimpleNamespace

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "settings", lambda: {"online_cache_gb": 12})
    monkeypatch.setattr(
        online.shutil, "disk_usage",
        lambda path: SimpleNamespace(
            total=128 * 1024**3,
            free=online.RESERVE_BYTES + 2 * online.MAX_DOWNLOAD - 1,
        ),
    )
    status = online.cache_status()
    assert status["paused"] and status["pause_reason"] == "disk"


def test_uncached_mov_is_never_streamed_as_mp4(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    entry = online._clip(
        "apple", "ocean", "https://sylvan.apple.com/Videos/ocean.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    monkeypatch.setattr(online, "lookup", lambda clip_id: entry)
    monkeypatch.setattr(online, "cached_file", lambda clip_id: None)
    fetched = []
    monkeypatch.setattr(online, "open_video_stream",
                        lambda *args: fetched.append(True))
    monkeypatch.setattr(online, "try_cache",
                        lambda entry: None)
    client = TestClient(main.app)
    response = client.get("/media/online/" + entry["id"])
    assert response.status_code == 503
    assert not fetched
