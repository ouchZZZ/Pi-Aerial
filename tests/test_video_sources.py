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
            assert client.post("/api/admin/display/play-once/stop", headers=csrf).status_code == 200
            assert client.get("/api/display/play-once").json() == {"stop": True}
    finally:
        with main.LOCK:
            main.DISPLAY_REQUESTS.clear()
