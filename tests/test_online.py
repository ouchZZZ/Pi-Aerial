"""Online video providers: tvOS 26, safe URLs and no-redownload history."""
from pathlib import Path

import pytest

from app import online, online_library
from app.online import try_cache as real_try_cache
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
                {"data": [{"nasa_id": "earth1", "title": "Earth from space timelapse"}]}]}}
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
    import plistlib

    data = json.dumps({"assets": [{"id": "new-clip"}]}).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("Customer/entries.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        strings = plistlib.dumps({"caption": "Антарктида"}, fmt=plistlib.FMT_BINARY)
        info = tarfile.TarInfo("TVIdleScreenStrings.bundle/ru.lproj/Localizable.nocache.strings")
        info.size = len(strings)
        archive.addfile(info, io.BytesIO(strings))

    class FakeResponse(io.BytesIO):
        pass

    monkeypatch.setattr(online.urllib.request, "urlopen",
                        lambda *args, **kwargs: FakeResponse(stream.getvalue()))
    manifest, strings, english = online._apple_manifest()
    assert manifest["assets"][0]["id"] == "new-clip"
    assert strings == {"caption": "Антарктида"}
    assert english == {}


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


def test_cache_maximum_tracks_disk_size(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app import storage

    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setattr(
        storage.shutil, "disk_usage",
        lambda path: SimpleNamespace(total=128 * 1024**3, free=80 * 1024**3),
    )
    assert storage.max_online_cache_gb() == 123
    storage.update_settings({"online_cache_gb": 100})
    with pytest.raises(ValueError, match="123"):
        storage.update_settings({"online_cache_gb": 124})


def test_source_settings_validation():
    from app.storage import DEFAULTS
    valid = dict(DEFAULTS["online_sources"])
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
                                                              "title": "ISS Earth timelapse"}]}]}}
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
    import threading
    # A previous TestClient may have an unfinished daemon download.
    monkeypatch.setattr(online, "DOWNLOAD_SLOT", threading.BoundedSemaphore(1))
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
    def fake_normalize(source, output, on_progress=None, force_transcode=False, max_bytes=None):
        assert force_transcode is False
        recorded.append(str(source))
        output.write_bytes(bytes.fromhex("000000186674797069736f6d") + b"M" * 2500)
        return "copy"
    monkeypatch.setattr(online, "normalize_video", fake_normalize)
    clip = online._clip(
        "apple", "ocean", "https://sylvan.apple.com/Videos/ocean.mov",
        "Ocean", "Apple", "https://www.apple.com/"
    )
    monkeypatch.setattr(online_library, "is_selected", lambda ident: ident == clip["id"])
    assert online.DOWNLOAD_SLOT.acquire(blocking=False)
    online._cache_worker(clip)
    path = tmp_path / (clip["id"] + ".mp4")
    assert path.is_file()
    assert path.read_bytes()[8:12] == b"isom"
    assert (tmp_path / (clip["id"] + ".ready")).read_text() == online.CACHE_PROFILE
    assert recorded
    assert not (tmp_path / (clip["id"] + ".part")).exists()
    assert clip["id"] in online._processed_ids()


def test_cache_worker_releases_its_original_slot(monkeypatch):
    import threading
    original = threading.BoundedSemaphore(1)
    assert original.acquire(blocking=False)
    replacement = threading.BoundedSemaphore(1)
    monkeypatch.setattr(online, "DOWNLOAD_SLOT", replacement)
    monkeypatch.setattr(online, "settings", lambda: {"online_cache_enabled": False})
    online._cache_worker({"id": "isolated-slot"}, original)
    assert original.acquire(blocking=False)
    with pytest.raises(ValueError):
        replacement.release()  # It must still be at its maximum value.


def test_nasa_http_manifest_links_are_upgraded(monkeypatch):
    def fake(url, **kw):
        if "/search?" in url:
            return {"collection": {"items": [{"data": [{"nasa_id": "earth",
                                                          "title": "Earth from space timelapse"}]}]}}
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
    online_library.set_selected([clip["id"]], True)
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
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
    online.atomic_json(online.CATALOG_FILE,
                       {"sources": {"apple": {"items": clips, "updated": 1}}})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": True, "nasa": False},
        "online_cache_enabled": True,
    })
    monkeypatch.setattr(online.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    online_library.set_selected([clip["id"] for clip in clips], True)
    calls = []
    monkeypatch.setattr(online, "try_cache", lambda entry: calls.append(entry["id"]) or True)
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
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {"apple": {"items": [clip]}}
    })
    monkeypatch.setattr(online.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    calls = []
    monkeypatch.setattr(online, "try_cache", lambda entry: calls.append(entry["id"]) or True)
    online.warm_cache()
    assert calls == []   # History does not grant automatic download anymore.
    online_library.set_selected([clip["id"]], True)
    online.warm_cache()
    assert calls == [clip["id"]]  # Selecting again can redownload despite history.


def test_cache_limit_pauses_without_eviction_then_resumes(monkeypatch, tmp_path):
    from types import SimpleNamespace

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
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
        video.truncate(1024**3)
    (cache / (old["id"] + ".ready")).write_text(online.CACHE_PROFILE)
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {"apple": {"items": [old, new]}}
    })

    status = online.cache_status()
    assert status["paused"] and status["pause_reason"] == "limit"
    assert status["used_bytes"] == 1024**3
    calls = []
    monkeypatch.setattr(online, "try_cache", lambda entry: calls.append(entry["id"]) or True)
    online.warm_cache()
    assert calls == []
    assert ready.exists()  # no eviction, even at the configured limit

    config["online_cache_gb"] = 2
    assert not online.cache_status()["paused"]
    online_library.set_selected([new["id"]], True)
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
            free=online.RESERVE_BYTES + 2048 - 1,
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


def test_file_limit_is_half_remaining_quota(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    monkeypatch.setattr(online, "settings", lambda: {"online_cache_gb": 12})
    monkeypatch.setattr(online, "_cache_used_bytes", lambda: 4 * 1024**3)
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=100 * 1024**3, total=128 * 1024**3))
    status = online.cache_status()
    assert status["file_limit_bytes"] == 4 * 1024**3
    assert not status["paused"]


def test_file_limit_reserves_disk_for_source_and_output(monkeypatch, tmp_path):
    from types import SimpleNamespace
    gib = 1024 ** 3
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    monkeypatch.setattr(online, "settings", lambda: {"online_cache_gb": 12})
    monkeypatch.setattr(online, "_cache_used_bytes", lambda: 0)
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=online.RESERVE_BYTES + 3 * gib,
                                                     total=128 * gib))
    status = online.cache_status()
    assert status["file_limit_bytes"] == 3 * gib // 2
    assert not status["paused"]


@pytest.mark.parametrize("declared", [True, False])
def test_worker_enforces_quota_with_or_without_content_length(monkeypatch, tmp_path, declared):
    import io
    import threading
    from types import SimpleNamespace
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    monkeypatch.setattr(online, "settings",
                        lambda: {"online_cache_enabled": True, "online_cache_gb": 12})
    monkeypatch.setattr(online, "cache_status",
                        lambda: {"paused": False, "file_limit_bytes": 2048})
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=100 * 1024**3))
    class Response(io.BytesIO):
        headers = {"Content-Length": "4096"} if declared else {}
    class Opener:
        def open(self, *args, **kwargs):
            return Response(b"x" * 4096)
    monkeypatch.setattr(online.urllib.request, "build_opener", lambda *a: Opener())
    converted = []
    monkeypatch.setattr(online, "normalize_video", lambda *a, **kw: converted.append(True))
    entry = online._clip("apple", "quota-boundary",
                         "https://sylvan.apple.com/quota.mov",
                         "Quota", "Apple", "https://www.apple.com/")
    slot = threading.BoundedSemaphore(1)
    slot.acquire()
    online._cache_worker(entry, slot)
    assert not converted
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.mp4"))
    assert slot.acquire(blocking=False)


def test_apple_keeps_english_library_metadata(monkeypatch):
    entry = {"id": "ocean", "localizedNameKey": "title",
             "pointsOfInterest": {"20": "later", "0": "caption"},
             "url-1080-H264": "https://sylvan.apple.com/ocean.mov"}
    monkeypatch.setattr(online, "_apple_manifest", lambda: (
        {"assets": [entry]},
        {"title": "Австралия", "caption": "Над океаном", "later": "Берег"},
        {"title": "Australia", "caption": "Over the ocean", "later": "Coast"}))
    clip = online._apple()[0]
    assert clip["name"] == "Австралия"
    assert clip["description"] == "Над океаном"
    assert clip["name_en"] == "Australia"
    assert clip["description_en"] == "Over the ocean"
    assert clip["captions"][0]["text"] == "Над океаном"


def test_cancelled_cache_worker_never_starts_network_and_releases_slot(monkeypatch):
    import threading

    stop = threading.Event()
    stop.set()
    slot = threading.BoundedSemaphore(1)
    assert slot.acquire(blocking=False)
    entry = {"id": "z" * 24}
    monkeypatch.setattr(online, "settings",
                        lambda: pytest.fail("Stopped worker attempted preparation"))
    online._cache_worker(entry, slot, stop)
    assert slot.acquire(blocking=False)
    assert entry["id"] not in online.ACTIVE


def test_robin_catalog_prefers_1080_and_keeps_attribution(monkeypatch):
    media = "https://github.com/RobinFrcd/AerialShots/releases/download/norway-2021-v1/"
    data = {"assets": [
        {"id": "a", "title": "Norway Coast",
         "pointsOfInterest": {"0": "Flying over a Norwegian fjord"},
         "url-4K-SDR": media + "fourk.m4v",
         "url-1080-H264": media + "hd.m4v"},
        {"id": "b", "title": "Mountain",
         "url-4K-SDR": media + "mountain4k.m4v"},
        {"id": "c", "title": "Untrusted",
         "url-1080-H264": "https://evil.example/bad.m4v"},
        {"id": "a", "title": "Duplicate", "url-4K-SDR": media + "duplicate.m4v"},
    ]}
    monkeypatch.setattr(online, "_http_json", lambda url, **kw: data)
    clips = online._robin()
    assert len(clips) == 2
    assert clips[0]["provider"] == "robin"
    assert clips[0]["remote_url"].endswith("/hd.m4v")
    assert clips[1]["remote_url"].endswith("/mountain4k.m4v")
    assert clips[0]["credit"].startswith("Robin Fourcade")
    assert clips[0]["description"] == "Flying over a Norwegian fjord"
    assert clips[0]["name_en"] == "Norway Coast"
    assert clips[0]["description_en"] == clips[0]["description"]


def test_robin_restricts_github_urls_and_redirects():
    base = "https://github.com/RobinFrcd/AerialShots/releases/download/"
    assert online.allowed_video_url(base + "reunion-2022/clip.m4v", "robin")
    assert online.allowed_video_url("https://release-assets.githubusercontent.com/asset?sig=abc", "robin")
    assert not online.allowed_video_url("https://github.com/anyone/other/releases/download/clip", "robin")
    assert not online.allowed_video_url("http://github.com/RobinFrcd/AerialShots/releases/download/a", "robin")
    assert not online.allowed_video_url("https://github.com.evil.test/a", "robin")
    assert not online.allowed_video_url("https://127.0.0.1/file.m4v", "robin")
    import urllib.request
    redirect = online.SafeRedirect("robin")
    req = urllib.request.Request(base + "reunion-2022/clip.m4v")
    with pytest.raises(ValueError, match="untrusted"):
        redirect.redirect_request(req, None, 302, "Moved", {},
                                  "https://evil.example/private.m4v")


def test_robin_clips_appear_only_after_normalization(monkeypatch, tmp_path):
    clip = online._clip(
        "robin", "reunion", "https://github.com/RobinFrcd/AerialShots/releases/download/"
        "reunion-2022/clip.m4v", "Reunion", "Robin Fourcade",
        "https://github.com/RobinFrcd/AerialShots", "Island")
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    online.atomic_json(online.CATALOG_FILE,
                       {"sources": {"robin": {"items": [clip], "updated": 1}}})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"robin": True},
    })
    assert online.lookup(clip["id"]) is None  # Suggestions are not playable.
    online_library.set_selected([clip["id"]], True)
    assert online.lookup(clip["id"]) == clip
    assert online.list_clips() == []


def test_old_settings_gain_robin_source_without_losing_choices(monkeypatch, tmp_path):
    from app import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    storage.atomic_json(tmp_path / "settings.json",
                        {"online_sources": {"apple": False, "nasa": True}})
    cfg = storage.settings()
    assert cfg["online_sources"] == {
        "apple": False, "nasa": True, "robin": True,
        **{name: False for name in online.ARCHIVE_THEMES},
    }
    storage.update_settings({"online_sources": {
        **cfg["online_sources"], "robin": False,
    }})
    assert storage.settings()["online_sources"]["robin"] is False


def test_all_sources_get_round_robin_turns_including_nasa_after_two(monkeypatch, tmp_path):
    """No source can hog the queue; NASA is not capped at two cached clips."""
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"robin": True, "apple": True, "nasa": True},
        "online_cache_enabled": True, "online_autodownload_enabled": True,
    })
    monkeypatch.setattr(online.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(online, "cache_status", lambda: {"paused": False})
    urls = {
        "robin": "https://github.com/RobinFrcd/AerialShots/releases/download/reunion-2022/",
        "apple": "https://sylvan.apple.com/Aerials/",
        "nasa": "https://images-assets.nasa.gov/video/",
    }
    clips = {
        src: [online._clip(src, str(i), urls[src] + str(i) + ".mp4",
                           src, src, "https://example.com") for i in range(4)]
        for src in ("robin", "apple", "nasa")
    }
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {src: {"items": items} for src, items in clips.items()}
    })
    # NASA already has two videos on disk, just like the reported Pi state.
    cached = {clip["id"] for clip in clips["nasa"][:2]}
    done = set()
    monkeypatch.setattr(online, "cached_file", lambda ident: tmp_path if ident in cached else None)
    monkeypatch.setattr(online, "_processed_ids", lambda: set(done))
    monkeypatch.setattr(online_library, "selected_ids", lambda: {
        clip["id"] for items in clips.values() for clip in items
    })
    scheduled = []

    def schedule(entry):
        scheduled.append(entry)
        done.add(entry["id"])
        cached.add(entry["id"])  # Simulate completed work, not just old processed history.
        return True

    monkeypatch.setattr(online, "try_cache", schedule)
    for _ in range(6):
        online.warm_cache()
    assert [item["provider"] for item in scheduled] == [
        "robin", "apple", "nasa", "robin", "apple", "nasa"
    ]
    assert [item["id"] for item in scheduled if item["provider"] == "nasa"] == [
        clips["nasa"][2]["id"], clips["nasa"][3]["id"]
    ]
    assert online.read_json(online.SCHEDULER_FILE)["next"] == "ia_nature"


def test_round_robin_cursor_survives_a_restart_and_skips_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"robin": False, "apple": True, "nasa": True},
        "online_cache_enabled": True,
    })
    monkeypatch.setattr(online.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(online, "cache_status", lambda: {"paused": False})
    clips = [
        online._clip("apple", "a", "https://sylvan.apple.com/a.mp4",
                     "Apple", "Apple", "https://apple.com"),
        online._clip("nasa", "n", "https://images-assets.nasa.gov/n.mp4",
                     "NASA", "NASA", "https://nasa.gov"),
    ]
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {"apple": {"items": [clips[0]]},
                    "nasa": {"items": [clips[1]]}}
    })
    done = set()
    monkeypatch.setattr(online, "_processed_ids", lambda: set(done))
    monkeypatch.setattr(online_library, "selected_ids", lambda: {
        item["id"] for item in clips
    })
    monkeypatch.setattr(online, "cached_file", lambda _: None)
    calls = []

    def schedule(entry):
        calls.append(entry["provider"])
        done.add(entry["id"])
        return True

    monkeypatch.setattr(online, "try_cache", schedule)
    online.warm_cache()   # Robin disabled: Apple gets the first turn
    assert online.read_json(online.SCHEDULER_FILE)["next"] == "nasa"
    online.warm_cache()   # Reads persisted cursor instead of resetting to Robin
    assert calls == ["apple", "nasa"]


def test_rejected_download_does_not_advance_round_robin(monkeypatch, tmp_path):
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "SCHEDULER_FILE", tmp_path / "scheduler.json")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"robin": True, "apple": False, "nasa": False},
        "online_cache_enabled": True,
    })
    monkeypatch.setattr(online.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(online, "cache_status", lambda: {"paused": False})
    clip = online._clip(
        "robin", "one",
        "https://github.com/RobinFrcd/AerialShots/releases/download/reunion-2022/one.m4v",
        "Robin", "Robin Fourcade", "https://github.com/RobinFrcd/AerialShots")
    online.atomic_json(online.CATALOG_FILE,
                       {"sources": {"robin": {"items": [clip]}}})
    monkeypatch.setattr(online, "_processed_ids", lambda: set())
    monkeypatch.setattr(online_library, "selected_ids", lambda: {clip["id"]})
    monkeypatch.setattr(online, "cached_file", lambda _: None)
    monkeypatch.setattr(online, "try_cache", lambda entry: False)  # Busy slot.
    online.warm_cache()
    assert not online.SCHEDULER_FILE.exists()
    monkeypatch.setattr(online, "try_cache", lambda entry: True)
    online.warm_cache()
    assert online.read_json(online.SCHEDULER_FILE)["next"] == "apple"


def test_internet_archive_url_hosts_reject_ssrf_and_other_archive_endpoints():
    src = "ia_history"
    assert online.allowed_video_url(
        "https://archive.org/download/historic-city/movie.mp4", src)
    assert online.allowed_video_url(
        "https://ia800304.us.archive.org/download/historic-city/movie.mp4", src)
    assert online.allowed_video_url(
        "https://ia800304.us.archive.org/14/items/historic-city/movie.mp4", src)
    # Internet Archive may route requests via Canadian DN storage nodes.
    assert online.allowed_video_url(
        "https://dn790001.ca.archive.org/0/items/historic-city/movie.mp4", src)
    assert online.allowed_video_url(
        "https://dn790001.ca.archive.org/0/items/historic-city/subfolder/movie.mp4", src)
    for url in [
        "http://archive.org/download/historic-city/video.mp4",
        "https://archive.org.evil.test/download/clip/v.mp4",
        "https://127.0.0.1/download/clip/v.mp4",
        "https://archive.org/metadata/historic-city",
        "https://archive.org/download/clip/../etc/passwd",
        "https://archive.org/download/clip/%2e%2e/passwd",
        "https://evil.us.archive.org/download/clip/v.mp4",
        "https://dn790001.ca.archive.org.evil.test/0/items/clip/video.mp4",
        "https://dn790001.ca.archive.org/0/items/clip/../secrets.mp4",
        "https://dn790001.ca.archive.org/0/items/clip/%2e%2e/secrets.mp4",
        "https://dn790001.ca.archive.org/0/items/clip//video.mp4",
        "https://dn790001.ca.archive.org/api/admin",
        "https://dn790001.ca.archive.org/0/items/../../private.mp4",
        "http://dn790001.ca.archive.org/0/items/clip/video.mp4",
        "https://127.0.0.1/0/items/clip/video.mp4",
        "https://localhost/0/items/clip/video.mp4",
    ]:
        assert not online.allowed_video_url(url, src), url



def test_archive_redirect_handler_allows_canadian_storage_and_diagnoses_unsafe_host():
    from urllib.request import Request
    request = Request("https://archive.org/download/historic-city/movie.mp4")
    handler = online.SafeRedirect("ia_history")
    accepted = "https://dn790001.ca.archive.org/0/items/historic-city/movie.mp4"
    redirect = handler.redirect_request(request, None, 302, "Found", {}, accepted)
    assert redirect.full_url == accepted

    # The diagnostic exposes only the host. Signed query strings are secret.
    with pytest.raises(ValueError, match="untrusted video host: files.evil.test"):
        handler.redirect_request(
            request, None, 302, "Found", {},
            "https://files.evil.test/foo.mp4?token=secret"
        )


def test_internet_archive_candidates_require_explicit_license_widescreen_hd():
    doc = {"identifier": "historical-city-1960",
           "title": "Old New York", "year": "1960"}
    good = {
        "metadata": {
            "title": "Streets of New York", "creator": "Archives",
            "description": "Wide city landscape", "year": "1960",
            "licenseurl": "https://creativecommons.org/licenses/by/4.0/"
        },
        "files": [
            {"name": "old_640x480.mp4", "size": "2000000"},
            {"name": "hd_1920x1080.mp4", "size": "3000000", "length": "250"},
            {"name": "fourk_3840x2160.mp4", "size": "5000000"},
        ],
    }
    clip = online._archive_candidate("ia_history", doc, good)
    assert clip is not None
    assert clip["remote_url"].endswith("/hd_1920x1080.mp4")
    assert clip["quality"] == "1920×1080"
    assert clip["year"] == "1960"
    assert clip["duration"] == 250
    assert clip["license"].endswith("/by/4.0/")
    assert clip["provider"] == "ia_history"

    from copy import deepcopy
    for change in [
        {"licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/"},
        {"licenseurl": "https://creativecommons.org/licenses/by-nd/4.0/"},
        {"licenseurl": ""},
    ]:
        payload = deepcopy(good)
        payload["metadata"].update(change)
        assert online._archive_candidate("ia_history", doc, payload) is None
    for filename in ["old_640x480.mp4", "vertical_1080x1920.mp4",
                     "too_small_1280x720.mp4", "square_1080x1080.mp4",
                     "known_720p.mp4", "known_480p.mp4"]:
        payload = deepcopy(good)
        payload["files"] = [{"name": filename, "size": "3000000"}]
        assert online._archive_candidate("ia_history", doc, payload) is None
    # Missing pixel dimensions are common: offer a proposal without claiming
    # it is HD. The actual file MUST pass ffprobe before being kept.
    payload = deepcopy(good)
    payload["files"] = [{"name": "mystery_movie.mp4", "size": "3000000"}]
    proposed = online._archive_candidate("ia_history", doc, payload)
    assert proposed is not None
    assert proposed["quality"] == "Неизвестно"
    assert proposed["quality_verified"] is False


def test_internet_archive_search_uses_bounded_metadata_only(monkeypatch):
    fetched = []
    identifier = "historic-city"
    def fake(url, **kwargs):
        fetched.append(url)
        if "/advancedsearch.php?" in url:
            return {"response": {"docs": [{"identifier": identifier,
                                            "title": "City 1960"}]}}
        assert url == "https://archive.org/metadata/" + identifier
        return {"metadata": {
            "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/",
            "description": "Historic city panorama"},
            "files": [{"name": "1960_1920x1080.mp4", "size": "2000000"}]}
    monkeypatch.setattr(online, "_http_json", fake)
    clips = online._archive_theme("ia_history")
    assert len(clips) == 1
    assert clips[0]["name"] == "City 1960"
    assert len(fetched) == len(online.ARCHIVE_SEARCHES["ia_history"]) + 1
    assert all("mediatype%3Amovies" in url for url in fetched[:-1])
    assert online.ARCHIVE_SCAN_REPORT["ia_history"]["scanned"] == 1
    assert online.ARCHIVE_SCAN_REPORT["ia_history"]["accepted"] == 1



def test_archive_search_rotates_pages_and_uses_independent_searches(monkeypatch, tmp_path):
    import urllib.parse
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    online.atomic_json(online.CATALOG_FILE, {
        "sources": {"ia_nature": {"scan_cursor": 1}}
    })
    searches = []
    def fake(url, **kwargs):
        if "/advancedsearch.php?" in url:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            searches.append(params)
            return {"response": {"docs": []}}
        raise AssertionError("No metadata requests for empty search pages")
    monkeypatch.setattr(online, "_http_json", fake)
    assert online._archive_theme("ia_nature") == []
    assert len(searches) == len(online.ARCHIVE_SEARCHES["ia_nature"])
    assert {query["page"][0] for query in searches} == {"2"}
    assert all(query["rows"] == ["35"] for query in searches)
    assert len({query["q"][0] for query in searches}) == len(searches)
    assert online.ARCHIVE_SCAN_REPORT["ia_nature"]["next_cursor"] == 2


def test_archive_worker_rejects_real_sd_video_even_if_catalog_claimed_hd(monkeypatch, tmp_path):
    import io
    import threading
    from types import SimpleNamespace
    from app import video_conversion
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(online, "CACHE_FAILED_UNTIL", {})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_cache_enabled": True, "online_autodownload_enabled": True,
        "online_cache_gb": 12})
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda _: SimpleNamespace(total=128*1024**3,
                                                  free=80*1024**3))
    monkeypatch.setattr(online.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    payload = b"\x00\x00\x00\x18ftypisom" + b"A" * 2048
    class FakeResponse(io.BytesIO):
        def __init__(self):
            super().__init__(payload)
            self.headers = {"Content-Length": str(len(payload))}
    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()
    monkeypatch.setattr(online.urllib.request, "build_opener",
                        lambda *args: FakeOpener())
    monkeypatch.setattr(video_conversion, "probe_video",
                        lambda _: {"width": 1280, "height": 720})
    monkeypatch.setattr(online, "normalize_video",
                        lambda *args, **kwargs: pytest.fail(
                            "SD footage was admitted to converter"))
    clip = online._clip(
        "ia_nature", "sample-clip",
        "https://archive.org/download/sample-clip/video.mp4",
        "Nature", "Uploader", "https://archive.org/details/sample-clip")
    slot = threading.BoundedSemaphore(1)
    assert slot.acquire(blocking=False)
    online._cache_worker(clip, slot)
    assert slot.acquire(blocking=False)
    assert not (cache / (clip["id"] + ".mp4")).exists()
    assert not (cache / (clip["id"] + ".ready")).exists()
    assert clip["id"] in online.CACHE_FAILED_UNTIL



def test_old_processed_history_cannot_block_selected_redownload(monkeypatch):
    import threading
    clip = online._clip("apple", "used", "https://sylvan.apple.com/old.mov",
                        "Old", "Apple", "https://www.apple.com/")
    ident = clip["id"]
    monkeypatch.setattr(online, "settings", lambda: {
        "online_cache_enabled": True, "online_autodownload_enabled": True,
    })
    monkeypatch.setattr(online, "cache_status", lambda: {"paused": False})
    monkeypatch.setattr(online.update_control, "paused", lambda: False)
    monkeypatch.setattr(online_library, "is_selected", lambda value: value == ident)
    monkeypatch.setattr(online, "cached_file", lambda _: None)
    monkeypatch.setattr(online, "_processed_ids", lambda: {ident})
    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(online, "DOWNLOAD_SLOT", slot)
    called = threading.Event()

    def worker(entry, borrowed, stop):
        with online.LOCK:
            online.ACTIVE.discard(entry["id"])
            online.CACHE_WORKERS.pop(threading.current_thread(), None)
            online.CANCEL_EVENTS.pop(entry["id"], None)
        borrowed.release()
        called.set()

    monkeypatch.setattr(online, "_cache_worker", worker)
    assert real_try_cache(clip) is True
    assert called.wait(timeout=2)
    assert slot.acquire(blocking=False)
    slot.release()


def test_download_failures_are_accurate_and_do_not_expose_addresses():
    import urllib.error
    for status, expected in [
        (403, "HTTP 403"), (404, "HTTP 404"),
        (429, "HTTP 429"), (503, "HTTP 503")
    ]:
        exc = urllib.error.HTTPError(
            "https://archive.org/download/test/private.mp4",
            status, "Forbidden", {}, None
        )
        detail = online._download_failure_message(exc)
        assert expected in detail
        assert "https://" not in detail
    assert "ожидания" in online._download_failure_message(
        urllib.error.URLError("timed out"))
    assert "HTTPS" in online._download_failure_message(
        urllib.error.URLError("SSL certificate verify failed"))
    exc = ValueError("Unexpected https://archive.org/download/signed?token=secret next")
    text = online._download_failure_message(exc)
    assert "secret" not in text
    assert "адрес скрыт" in text



def test_catalog_refresh_interval_defaults_to_sixty_minutes_and_can_change(
        monkeypatch, tmp_path):
    clock = [1_000_000.0]
    config = {"online_sources": {"apple": True},
              "online_refresh_interval_minutes": 60}
    clip = online._clip("apple", "demo", "https://sylvan.apple.com/demo.mp4",
                        "Demo", "Apple", "https://www.apple.com/")
    calls = []
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "settings", lambda: config)
    monkeypatch.setattr(online, "FETCHERS",
                        {"apple": lambda: calls.append(clock[0]) or [clip]})
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    monkeypatch.setattr(online.time, "time", lambda: clock[0])

    online.refresh_catalog()
    assert len(calls) == 1
    clock[0] += 59 * 60
    online.refresh_catalog()
    assert len(calls) == 1
    clock[0] += 60
    online.refresh_catalog()
    assert len(calls) == 2

    # Changing the setting takes effect on the next poll, without restart.
    config["online_refresh_interval_minutes"] = 120
    clock[0] += 119 * 60
    online.refresh_catalog()
    assert len(calls) == 2
    clock[0] += 60
    online.refresh_catalog()
    assert len(calls) == 3

    # Manual refresh retains the existing one-minute safeguard.
    clock[0] += 30
    online.refresh_catalog(force=True)
    assert len(calls) == 3
    clock[0] += 31
    online.refresh_catalog(force=True)
    assert len(calls) == 4


def test_online_refresh_interval_persists_and_rejects_bad_values(
        monkeypatch, tmp_path):
    from app import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    assert storage.settings()["online_refresh_interval_minutes"] == 60
    assert storage.update_settings({
        "online_refresh_interval_minutes": 120
    })["online_refresh_interval_minutes"] == 120
    assert storage.settings()["online_refresh_interval_minutes"] == 120
    for invalid in [0, -1, True, 5.5, "120", None]:
        with pytest.raises(ValueError, match="Интервал проверки видеоисточников"):
            storage.update_settings({"online_refresh_interval_minutes": invalid})
    assert storage.settings()["online_refresh_interval_minutes"] == 120


def test_refresh_skips_disabled_sources_at_new_interval(monkeypatch, tmp_path):
    clock = [1_000_000.0]
    calls = []
    clips = {
        name: online._clip(name, "one",
            ("https://sylvan.apple.com/a.mp4" if name == "apple"
             else "https://images-assets.nasa.gov/video/a.mp4"),
            "Sample", name, "https://example.com/")
        for name in ("apple", "nasa")
    }
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": False, "nasa": True},
        "online_refresh_interval_minutes": 1,
    })
    monkeypatch.setattr(online, "FETCHERS", {
        key: (lambda key=key: calls.append(key) or [clips[key]])
        for key in ("apple", "nasa")
    })
    monkeypatch.setattr(online, "warm_cache", lambda: None)
    monkeypatch.setattr(online.time, "time", lambda: clock[0])
    online.refresh_catalog()
    clock[0] += 61
    online.refresh_catalog()
    assert calls == ["nasa", "nasa"]


def test_background_and_manual_catalog_checks_are_serialized(monkeypatch, tmp_path):
    import threading
    import time

    started = threading.Event()
    release = threading.Event()
    requests = []
    clip = online._clip("apple", "race", "https://sylvan.apple.com/race.mp4",
                        "Race", "Apple", "https://www.apple.com/")
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"apple": True},
        "online_refresh_interval_minutes": 60,
    })
    monkeypatch.setattr(online, "warm_cache", lambda: None)

    def slow_fetch():
        requests.append("apple")
        started.set()
        if not release.wait(3):
            raise AssertionError("test release timed out")
        return [clip]

    monkeypatch.setattr(online, "FETCHERS", {"apple": slow_fetch})
    first = threading.Thread(target=online.refresh_catalog)
    second = threading.Thread(target=online.refresh_catalog)
    first.start()
    assert started.wait(2)
    second.start()
    try:
        time.sleep(0.05)
        assert len(requests) == 1  # A second scan cannot overlap.
    finally:
        release.set()
        first.join(timeout=3)
        second.join(timeout=3)
    assert not first.is_alive() and not second.is_alive()
    assert requests == ["apple"]  # Recheck reads the first scan's timestamp.



def test_archive_metadata_filters_irrelevant_and_upscaled_content():
    good = {"licenseurl": "https://creativecommons.org/licenses/by/4.0/",
            "title": "Mountain Fog Time Lapse",
            "description": "Aerial landscape in the morning"}
    def candidate(provider, title, description="", length="140"):
        doc = {"identifier": "sample-metadata", "title": title}
        data = {"metadata": {**good, "title": title,
                             "description": description},
                "files": [{"name": "clip_1920x1080.mp4", "size": "4000000",
                           "length": length}]}
        return online._archive_candidate(provider, doc, data)
    assert candidate("ia_nature", "Mountain Fog", "Aerial misty landscape")
    assert candidate("ia_timelapse", "Starry Sky Time Lapse",
                     "Night sky timelapse")
    assert candidate("ia_space", "Earth from ISS",
                     "NASA orbital Earth imagery")
    assert candidate("ia_history", "Old New York",
                     "Historic city streets")
    assert candidate("ia_cities", "Paris skyline", "Drone aerial view")

    for provider, title, description in [
        ("ia_nature", "Nature Cat - Full Series", "Nature videos"),
        ("ia_space", "Hackers on Planet Earth", "Conference video"),
        ("ia_space", "Dr. Moze - Yoga for Earth Girls", "Earth yoga class"),
        ("ia_space", "Clutch Cargo cartoons collection", "Space animation"),
        ("ia_history", "Sneakers on Power Lines", "Clothing"),
        ("ia_nature", "Old Nature Show", "Upscaled from 480p to 1080p"),
        ("ia_cities", "Gameplay: City Skyline", "Recorded video game"),
    ]:
        assert candidate(provider, title, description) is None, title


def test_archive_minimum_duration_is_sixty_seconds_for_known_metadata():
    doc = {"identifier": "river-scenery", "title": "Mountain river scenery"}
    def payload(runtime=None, length=None):
        metadata = {
            "title": "Mountain river scenery",
            "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
        }
        if runtime is not None:
            metadata["runtime"] = runtime
        file = {"name": "river_1920x1080.mp4", "size": "4000000"}
        if length is not None:
            file["length"] = length
        return {"metadata": metadata, "files": [file]}
    assert online._archive_candidate("ia_nature", doc, payload(length="59")) is None
    assert online._archive_candidate("ia_nature", doc, payload(length="60")) is not None
    assert online._archive_candidate("ia_nature", doc, payload(runtime="00:00:07")) is None
    assert online._archive_candidate("ia_nature", doc, payload(runtime="00:02:40"))
    unknown = online._archive_candidate("ia_nature", doc, payload())
    assert unknown is not None and unknown["duration"] is None
    assert online._archive_duration_seconds("00:01:30") == 90



def test_archive_worker_rejects_real_five_second_file_before_conversion(monkeypatch, tmp_path):
    import io
    import threading
    from types import SimpleNamespace
    from app import video_conversion
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(online, "CACHE_FAILED_UNTIL", {})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_cache_enabled": True, "online_autodownload_enabled": True,
        "online_cache_gb": 12})
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda _: SimpleNamespace(total=128*1024**3,
                                                  free=80*1024**3))
    monkeypatch.setattr(online.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    data = b"\x00\x00\x00\x18ftypisom" + b"V" * 2048
    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}
    class Opener:
        def open(self, req, timeout):
            return Response()
    monkeypatch.setattr(online.urllib.request, "build_opener",
                        lambda *args: Opener())
    monkeypatch.setattr(video_conversion, "probe_video",
                        lambda _: {"width": 1920, "height": 1080})
    monkeypatch.setattr(video_conversion, "probe_duration", lambda _: 5.0)
    monkeypatch.setattr(online, "normalize_video",
                        lambda *args, **kwargs: pytest.fail(
                            "Short video was incorrectly admitted to converter"))
    clip = online._clip("ia_timelapse", "short-recording",
        "https://archive.org/download/short-recording/video.mp4",
        "Starry Sky Time Lapse", "Uploader",
        "https://archive.org/details/short-recording")
    slot = threading.BoundedSemaphore(1)
    assert slot.acquire(blocking=False)
    online._cache_worker(clip, slot)
    assert slot.acquire(blocking=False)
    assert not (cache / (clip["id"] + ".mp4")).exists()
    assert "длительность" in online.CACHE_FAILED_REASONS[clip["id"]]


def _nasa_item(sid: str, title: str, description: str = "", **extra):
    return {"data": [{"nasa_id": sid, "media_type": "video", "title": title,
                       "description": description, **extra}]}


def test_nasa_metadata_prefers_scenery_over_generic_news_and_animation():
    assert online._nasa_metadata_score(
        _nasa_item("good", "Earth From Space Timelapse: Aurora")) > 8
    assert online._nasa_metadata_score(
        _nasa_item("generic", "Earth")) == 0
    assert online._nasa_metadata_score(
        _nasa_item("talk", "ISS Earth Interview with Astronauts")) == 0
    assert online._nasa_metadata_score(
        _nasa_item("synth", "Earth From Space Timelapse Animation")) == 0
    assert online._nasa_metadata_score(
        _nasa_item("short", "Earth From Space Timelapse", duration="00:00:13")) == 0
    assert online._nasa_metadata_score(
        _nasa_item("wrong-media", "Earth From Space Timelapse",
                   media_type="image")) == 0


def test_nasa_manifest_chooses_hd_and_rejects_small_preview(monkeypatch):
    entry = _nasa_item("hd", "Earth from space timelapse")
    base = "https://images-assets.nasa.gov/video/hd/"
    def fake(url, **kw):
        assert "/asset/hd" in url
        return {"collection": {"items": [
            {"href": base + "hd~small.mp4"},
            {"href": base + "hd~medium.mp4"},
            {"href": base + "hd~orig.mp4"},
            {"href": base + "hd_1080p.mp4"},
            {"href": "https://untrusted.invalid/clip_1080p.mp4"},
        ]}}
    monkeypatch.setattr(online, "_http_json", fake)
    clip, status = online._nasa_manifest(entry)
    assert status == "ok"
    assert clip["remote_url"] == base + "hd_1080p.mp4"
    monkeypatch.setattr(online, "_http_json",
                        lambda *a, **kw: {"collection": {"items": [
                            {"href": base + "hd~small.mp4"},
                            {"href": base + "hd~medium.mp4"}]}})
    assert online._nasa_manifest(entry) == (None, "no_hd_candidate")


def test_nasa_search_reaches_second_page_and_ignores_interviews(monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    queried = []
    base = "https://images-assets.nasa.gov/video/good/good~orig.mp4"
    def fake(url, **kw):
        if "/asset/" in url:
            return {"collection": {"items": [{"href": base}]}}
        args = parse_qs(urlsplit(url).query)
        queried.append(args)
        if args["q"][0] != online.NASA_SEARCH_TERMS[0]:
            return {"collection": {"items": []}}
        if args["page"][0] == "1":
            return {"collection": {"items": [
                _nasa_item("bad", "NASA Press Conference: Earth from Space Timelapse")
                for _ in range(online.NASA_PAGE_SIZE)]}}
        return {"collection": {"items": [
            _nasa_item("good", "Earth from space timelapse and aurora")]}}
    monkeypatch.setattr(online, "_http_json", fake)
    clips = online._nasa()
    assert [clip["name"] for clip in clips] == [
        "Earth from space timelapse and aurora"]
    assert any(args["page"] == ["2"] for args in queried)
    assert online.NASA_SCAN_REPORT["rejected_metadata"] >= online.NASA_PAGE_SIZE
    assert online.NASA_SCAN_REPORT["accepted"] == 1


def test_nasa_search_is_sorted_by_relevance_not_manifest_completion(monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    def fake(url, **kw):
        if "/asset/" in url:
            sid = url.rsplit("/", 1)[-1]
            return {"collection": {"items": [
                {"href": f"https://images-assets.nasa.gov/video/{sid}/{sid}~orig.mp4"}]}}
        args = parse_qs(urlsplit(url).query)
        if args["q"][0] == online.NASA_SEARCH_TERMS[0]:
            return {"collection": {"items": [
                _nasa_item("medium", "Clouds over Earth horizon"),
                _nasa_item("best", "Earth from space timelapse aurora")]}}
        return {"collection": {"items": []}}
    monkeypatch.setattr(online, "_http_json", fake)
    assert [clip["name"] for clip in online._nasa()] == [
        "Earth from space timelapse aurora", "Clouds over Earth horizon"]


@pytest.mark.parametrize("width,height,duration", [
    (1280, 720, 120), (1080, 1920, 120), (1920, 1080, 8),
    (1920, 1080, 2000), (1920, 1080, None)])
def test_nasa_real_quality_gate_rejects_bad_input(monkeypatch, tmp_path,
                                                   width, height, duration):
    from app import video_conversion
    monkeypatch.setattr(video_conversion, "probe_video",
                        lambda path: {"width": width, "height": height})
    monkeypatch.setattr(video_conversion, "probe_duration",
                        lambda path: duration)
    with pytest.raises(ValueError, match="NASA:"):
        online._verify_scenic_video(tmp_path / "candidate.mp4", "nasa")


def test_nasa_real_quality_gate_accepts_full_hd(monkeypatch, tmp_path):
    from app import video_conversion
    monkeypatch.setattr(video_conversion, "probe_video",
                        lambda path: {"width": 1920, "height": 1080})
    monkeypatch.setattr(video_conversion, "probe_duration", lambda path: 100.0)
    online._verify_scenic_video(tmp_path / "candidate.mp4", "nasa")


def test_nasa_cache_migration_discards_only_bad_nasa_files(monkeypatch, tmp_path):
    from app import video_conversion
    cache = tmp_path / "cache"
    cache.mkdir()
    marker = tmp_path / "nasa-migration.json"
    catalog = tmp_path / "catalog.json"
    good = online._clip("nasa", "valid", "https://images-assets.nasa.gov/good.mp4",
                        "Earth from space timelapse", "NASA", "https://images.nasa.gov")
    bad = online._clip("nasa", "bad", "https://images-assets.nasa.gov/bad.mp4",
                       "Earth horizon", "NASA", "https://images.nasa.gov")
    apple = online._clip("apple", "valid", "https://sylvan.apple.com/apple.mp4",
                         "Apple", "Apple", "https://apple.com")
    for clip in (good, bad, apple):
        (cache / (clip["id"] + ".mp4")).write_bytes(b"cached")
        (cache / (clip["id"] + ".ready")).write_text(online.CACHE_PROFILE)
    monkeypatch.setattr(online, "CACHE_FOLDER", cache)
    monkeypatch.setattr(online, "CATALOG_FILE", catalog)
    monkeypatch.setattr(online, "NASA_CACHE_CHECK_MARKER", marker)
    monkeypatch.setattr(online.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(video_conversion, "probe_video",
                        lambda path: {"width": 1280 if bad["id"] in str(path) else 1920,
                                      "height": 720 if bad["id"] in str(path) else 1080})
    monkeypatch.setattr(video_conversion, "probe_duration", lambda path: 120)
    online.atomic_json(catalog, {"sources": {"nasa": {"items": [good, bad]},
                                             "apple": {"items": [apple]}}})
    online.verify_old_nasa_cache_once()
    assert marker.exists()
    assert (cache / (good["id"] + ".mp4")).exists()
    assert not (cache / (bad["id"] + ".mp4")).exists()
    assert (cache / (apple["id"] + ".mp4")).exists()
    assert online.read_json(marker)["discarded"] == 1


def test_nasa_refresh_preserves_selected_old_entries(monkeypatch, tmp_path):
    prior = online._clip("nasa", "selected", "https://images-assets.nasa.gov/old.mp4",
                         "Old selected", "NASA", "https://images.nasa.gov")
    fresh = online._clip("nasa", "new", "https://images-assets.nasa.gov/new.mp4",
                         "New scenery", "NASA", "https://images.nasa.gov")
    catalog = tmp_path / "catalog.json"
    online.atomic_json(catalog, {"sources": {"nasa": {"items": [prior]}}})
    monkeypatch.setattr(online, "CATALOG_FILE", catalog)
    monkeypatch.setattr(online, "FETCHERS", {"nasa": lambda: [fresh]})
    monkeypatch.setattr(online, "settings", lambda: {
        "online_sources": {"nasa": True}, "online_refresh_interval_minutes": 60})
    monkeypatch.setattr(online, "warm_cache", lambda **kw: None)
    monkeypatch.setattr(online_library, "selected_ids", lambda: {prior["id"]})
    monkeypatch.setattr(online, "NASA_SCAN_REPORT", {"accepted": 1, "scanned": 12})
    online.refresh_catalog(force=True)
    source = online.read_json(catalog)["sources"]["nasa"]
    assert {x["id"] for x in source["items"]} == {prior["id"], fresh["id"]}
    assert source["scan"]["scanned"] == 12
    assert next(x for x in online.source_status() if x["id"] == "nasa")["scan"]["accepted"] == 1



def test_archive_transfer_resumes_at_verified_byte_offset(monkeypatch, tmp_path):
    import io
    import threading
    from types import SimpleNamespace
    complete = b"\x00\x00\x00\x18ftypisom" + b"A" * 3588
    split = 850
    requests = []

    class FakeResponse(io.BytesIO):
        def __init__(self, body, status, headers, fail=False):
            super().__init__(body)
            self.status = status
            self.headers = headers
            self.fail = fail

        def read(self, n=-1):
            if self.fail and self.tell() >= split:
                raise ConnectionResetError("Archive mirror closed connection")
            return super().read(min(n, split) if self.fail else n)

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if len(requests) == 1:
                return FakeResponse(
                    complete[:split], 200,
                    {"Content-Length": str(len(complete)), "ETag": '"original"'},
                    fail=True)
            assert request.get_header("Range") == f"bytes={split}-"
            assert request.get_header("If-range") == '"original"'
            rest = complete[split:]
            return FakeResponse(rest, 206, {
                "Content-Range": f"bytes {split}-{len(complete)-1}/{len(complete)}",
                "Content-Length": str(len(rest)),
            })

    monkeypatch.setattr(online.time, "sleep", lambda _: None)
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=80*1024**3))
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    temp = tmp_path / "sample.part"
    size = online._download_archive_with_resume(
        Opener(), "https://archive.org/download/item/video.mp4",
        temp, len(complete) + 1024, threading.Event(), "a" * 24)
    assert size == len(complete)
    assert temp.read_bytes() == complete
    assert len(requests) == 2


def test_archive_transfer_restarts_if_server_ignores_range(monkeypatch, tmp_path):
    import io
    import threading
    from types import SimpleNamespace

    complete = b"ftyp" * 600
    split = 500
    requests = []

    class FakeResponse(io.BytesIO):
        def __init__(self, data, headers, fail=False):
            super().__init__(data)
            self.headers = headers
            self.status = 200
            self.fail = fail

        def read(self, n=-1):
            if self.fail and self.tell() > 0:
                raise TimeoutError("read stalled")
            return super().read(min(n, split) if self.fail else n)

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if len(requests) == 1:
                return FakeResponse(complete[:split],
                                    {"Content-Length": str(len(complete))},
                                    fail=True)
            assert request.get_header("Range") == f"bytes={split}-"
            return FakeResponse(complete, {"Content-Length": str(len(complete))})

    monkeypatch.setattr(online.time, "sleep", lambda _: None)
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=80*1024**3))
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    temp = tmp_path / "sample.part"
    total = online._download_archive_with_resume(
        Opener(), "https://archive.org/download/item/video.mp4",
        temp, len(complete) + 1024, threading.Event(), "a" * 24)
    assert total == len(complete)
    assert temp.read_bytes() == complete
    assert len(requests) == 2


def test_archive_rejects_incorrect_resume_offset(monkeypatch, tmp_path):
    import io
    import threading
    from types import SimpleNamespace

    class FakeResponse(io.BytesIO):
        def __init__(self, data, headers, status):
            super().__init__(data)
            self.headers = headers
            self.status = status

        def read(self, n=-1):
            if self.status == 200 and self.tell() >= 200:
                raise ConnectionResetError("disconnect")
            return super().read(min(200, n) if self.status == 200 else n)

    class Opener:
        def __init__(self):
            self.calls = 0

        def open(self, request, timeout):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(b"X" * 200, {"Content-Length": "1000"}, 200)
            return FakeResponse(
                b"Y" * 799,
                {"Content-Range": "bytes 201-999/1000",
                 "Content-Length": "799"}, 206)

    monkeypatch.setattr(online.time, "sleep", lambda _: None)
    monkeypatch.setattr(online.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=80*1024**3))
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    opener = Opener()
    with pytest.raises(ValueError, match="несовпадение частей файла"):
        online._download_archive_with_resume(
            opener, "https://archive.org/download/item/video.mp4",
            tmp_path / "sample.part", 8192, threading.Event(), "a" * 24)
    assert opener.calls == 2


def test_archive_retry_is_bounded_and_never_retries_access_denied(monkeypatch, tmp_path):
    import threading
    import urllib.error
    monkeypatch.setattr(online.time, "sleep", lambda _: None)

    class Opener:
        def __init__(self, error):
            self.calls = 0
            self.error = error

        def open(self, request, timeout):
            self.calls += 1
            raise self.error

    opener = Opener(ConnectionResetError("reset"))
    with pytest.raises(ConnectionResetError):
        online._download_archive_with_resume(
            opener, "https://archive.org/download/item/video.mp4",
            tmp_path / "sample.part", 8192, threading.Event(), "a" * 24)
    assert opener.calls == online.ARCHIVE_DOWNLOAD_ATTEMPTS

    error = urllib.error.HTTPError(
        "https://archive.org/download/item/video.mp4", 403,
        "Forbidden", {}, None)
    rejected = Opener(error)
    with pytest.raises(urllib.error.HTTPError):
        online._download_archive_with_resume(
            rejected, "https://archive.org/download/item/video.mp4",
            tmp_path / "sample2.part", 8192, threading.Event(), "b" * 24)
    assert rejected.calls == 1


def test_archive_retry_stops_when_user_cancels(monkeypatch, tmp_path):
    import threading
    stop = threading.Event()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(1)
            raise ConnectionResetError("reset")

    def cancel(_):
        stop.set()

    monkeypatch.setattr(online.time, "sleep", cancel)
    with pytest.raises(InterruptedError):
        online._download_archive_with_resume(
            Opener(), "https://archive.org/download/item/video.mp4",
            tmp_path / "sample.part", 8192, stop, "a" * 24)
    assert len(calls) == 1
