"""Manual URL playback must accept only supported provider-owned links."""
import pytest
from fastapi.testclient import TestClient

from app import auth, main, video_sources


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
])
def test_recognizes_youtube_url_formats(url):
    source = video_sources.resolve(url)
    assert source.provider == "youtube"
    assert source.video_id == "dQw4w9WgXcQ"
    assert video_sources.describe(source.provider, source.video_id) == {
        "kind": "embed", "provider": "youtube", "video_id": "dQw4w9WgXcQ"
    }


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
    "https://youtu.be.evil.example/dQw4w9WgXcQ",
    "https://127.0.0.1/watch?v=dQw4w9WgXcQ",
    "file:///etc/passwd",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/watch?v=not-valid",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=abcdefghijk",
    "https://user:pass@youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com:444/watch?v=dQw4w9WgXcQ",
    "https://example.com/video.mp4",
    "https://youtube.com/watch?v=dQw4w9WgXcQ\nmalicious",
    "x" * 2049,
])
def test_rejects_untrusted_or_unsupported_urls(url):
    with pytest.raises(video_sources.InvalidVideoURL):
        video_sources.resolve(url)


def test_url_playback_requires_admin_and_respects_stop(monkeypatch):
    def idle(stop):
        stop.wait(20)
    monkeypatch.setattr(main, "_online_loop", idle)
    monkeypatch.setattr(main.weather, "polling_loop", idle)
    monkeypatch.setattr(main.local_videos, "polling_loop", idle)
    monkeypatch.setattr(main.github_updates, "polling_loop", idle)
    auth.set_password("a-safe-test-password-123")
    url = {"url": "https://youtu.be/dQw4w9WgXcQ"}
    with main.LOCK:
        main.DISPLAY_REQUESTS.clear()
    try:
        with TestClient(main.app) as client:
            endpoint = "/api/admin/display/play-once/url"
            assert client.post(endpoint, json=url).status_code == 401
            login = client.post("/api/auth/login", json={"password": "a-safe-test-password-123"})
            assert login.status_code == 200
            csrf = {"x-csrf-token": login.json()["csrf"]}
            assert client.post(endpoint, json=url).status_code == 403
            assert client.post(endpoint, json={"url": "https://evil.test/"}, headers=csrf).status_code == 422
            response = client.post(endpoint, json=url, headers=csrf)
            assert response.status_code == 200, response.text
            assert response.json()["provider"] == "youtube"

            command = client.get("/api/display/play-once").json()
            assert command["kind"] == "embed"
            assert command["provider"] == "youtube"
            assert command["video_id"] == "dQw4w9WgXcQ"
            assert "url" not in command  # Never expose raw arbitrary external URLs.
            assert client.get("/api/display/play-once/" + command["id"]).status_code == 404
            status_endpoint = "/api/admin/display/play-once/status"
            assert client.get(status_endpoint).json()["status"]["stage"] == "requested"
            report_endpoint = "/api/display/play-once/report"
            assert client.post(report_endpoint, json={"id": "unknown-token", "stage": "error"}).status_code == 404
            assert client.post(report_endpoint, json={
                "id": command["id"], "stage": "playing"
            }).status_code == 200
            assert client.get(status_endpoint).json()["status"]["stage"] == "playing"
            assert client.post(report_endpoint, json={
                "id": command["id"], "stage": "error", "detail": "YouTube error 153"
            }).status_code == 200
            assert client.get(status_endpoint).json()["status"]["detail"] == "YouTube error 153"
            assert client.post(report_endpoint, json={
                "id": command["id"], "stage": "error", "detail": "x" * 200
            }).status_code == 422
            assert client.post(report_endpoint, json={
                "id": command["id"], "stage": "invalid"
            }).status_code == 422
            assert client.post("/api/admin/display/play-once/stop", headers=csrf).status_code == 200
            assert client.get(status_endpoint).json()["status"]["stage"] == "stopped"
            assert client.post(report_endpoint, json={
                "id": command["id"], "stage": "error", "detail": "stale"
            }).status_code == 404
            assert client.get("/api/display/play-once").json() == {"stop": True}
    finally:
        with main.LOCK:
            main.DISPLAY_REQUESTS.clear()


@pytest.mark.parametrize("url, expected", [
    ("https://vkvideo.ru/video-219483134_456253880", "-219483134_456253880"),
    ("https://vk.com/video653990375_456241243", "653990375_456241243"),
    ("https://vk.ru/video-219483134_456253880", "-219483134_456253880"),
    ("https://m.vk.com/video-219483134_456253880?t=10", "-219483134_456253880"),
    ("https://www.vkvideo.ru/video-219483134_456253880/", "-219483134_456253880"),
    ("https://vk.com/video?z=video-219483134_456253880%2Fpl_-111_222", "-219483134_456253880"),
    ("https://vk.ru/example?z=video653990375_456241243", "653990375_456241243"),
    ("https://vkvideo.ru/video_ext.php?oid=-219483134&id=456253880&hd=4",
     "-219483134_456253880"),
    ("https://vkvideo.ru/video_ext.php?oid=-219483134&id=456253880&hash=d79c769f2d5d3f53&hd=3",
     "-219483134_456253880~d79c769f2d5d3f53"),
    ("https://vk.com/video_ext.php?oid=653990375&id=456241243&hash=A3F5&js_api=1",
     "653990375_456241243~A3F5"),
])
def test_recognizes_vk_video_formats(url, expected):
    source = video_sources.resolve(url)
    assert source.provider == "vkvideo"
    assert source.video_id == expected
    info = video_sources.describe(source.provider, source.video_id)
    assert info["kind"] == "embed"
    assert info["provider"] == "vkvideo"
    assert info["video_id"] == ("456241243" if expected.startswith("653") else "456253880")
    if "~" in expected:
        assert info["hash"] == expected.split("~", 1)[1]
    else:
        assert "hash" not in info


@pytest.mark.parametrize("url", [
    "http://vkvideo.ru/video-219483134_456253880",
    "https://vkvideo.ru.attacker.example/video-219483134_456253880",
    "https://vkvideo.ru@attacker.example/video-219483134_456253880",
    "https://vkvideo.ru:8080/video-219483134_456253880",
    "https://vkvideo.ru/other-page",
    "https://vk.com/video",
    "https://vkvideo.ru/clip-219483134_456253880",
    "https://vkvideo.ru/video_ext.php?id=456253880",
    "https://vkvideo.ru/video_ext.php?oid=-219483134",
    "https://vkvideo.ru/video_ext.php?oid=-219483134&id=456253880&id=123",
    "https://vkvideo.ru/video_ext.php?oid=-219483134&id=456253880&hash=x!!",
    "https://vkvideo.ru/video_ext.php?oid=-219483134&id=456253880&hash=a&hash=b",
    "https://vk.com/video?z=video-219483134_456253880&z=video-111_222",
    "https://vk.com/video?z=https%3A%2F%2Fevil.example%2Fvideo-219483134_456253880",
    "https://vkvideo.ru/video-219483134_456253880%0Ainjected",
])
def test_rejects_bad_vk_video_urls(url):
    with pytest.raises(video_sources.InvalidVideoURL):
        video_sources.resolve(url)


def test_vk_video_request_over_admin_api(monkeypatch):
    def idle(stop):
        stop.wait(20)
    monkeypatch.setattr(main, "_online_loop", idle)
    monkeypatch.setattr(main.weather, "polling_loop", idle)
    monkeypatch.setattr(main.local_videos, "polling_loop", idle)
    monkeypatch.setattr(main.github_updates, "polling_loop", idle)
    auth.set_password("vk-video-test-password-123")
    with main.LOCK:
        main.DISPLAY_REQUESTS.clear()
    try:
        with TestClient(main.app) as client:
            endpoint = "/api/admin/display/play-once/url"
            payload = {
                "url": "https://vkvideo.ru/video_ext.php?oid=-219483134&id=456253880&hash=abc123"
            }
            assert client.post(endpoint, json=payload).status_code == 401
            login = client.post("/api/auth/login", json={"password": "vk-video-test-password-123"})
            csrf = {"x-csrf-token": login.json()["csrf"]}
            assert client.post(endpoint, json=payload).status_code == 403
            response = client.post(endpoint, json=payload, headers=csrf)
            assert response.status_code == 200, response.text
            assert response.json()["provider"] == "vkvideo"
            command = client.get("/api/display/play-once").json()
            assert command["kind"] == "embed"
            assert command["provider"] == "vkvideo"
            assert command["owner_id"] == "-219483134"
            assert command["video_id"] == "456253880"
            assert command["hash"] == "abc123"
            assert "url" not in command
            assert client.get("/api/display/play-once/" + command["id"]).status_code == 404
            assert client.post("/api/admin/display/play-once/stop", headers=csrf).status_code == 200
            assert client.get("/api/display/play-once").json() == {"stop": True}
    finally:
        with main.LOCK:
            main.DISPLAY_REQUESTS.clear()
