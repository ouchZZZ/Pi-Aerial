"""Live video telemetry: observable, protected and without fabricated percentages."""
import io
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import video_progress as p
from app import video_conversion as vc


@pytest.fixture(autouse=True)
def isolated_progress():
    with p.LOCK:
        p.TASKS.clear()
    yield
    with p.LOCK:
        p.TASKS.clear()


def test_download_bytes_speed_and_percentage(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(p.time, "monotonic", lambda: clock[0])
    p.start("online", "clip1", "Ocean", "apple")
    clock[0] += 1
    p.download("online", "clip1", 100000, 200000)
    task = p.snapshot()["active"][0]
    assert task["stage"] == "downloading"
    assert task["percent"] == 50
    assert task["bytes_received"] == 100000
    assert task["bytes_total"] == 200000
    assert task["speed_bps"] == 100000
    assert task["provider"] == "apple"
    assert "remote_url" not in task
    clock[0] += 6
    assert p.snapshot()["active"][0]["speed_bps"] == 0


def test_unknown_size_and_duration_do_not_invent_percent(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(p.time, "monotonic", lambda: clock[0])
    p.start("online", "clip2", "Earth", "nasa")
    clock[0] += 1
    p.download("online", "clip2", 1024, None)
    assert p.snapshot()["active"][0]["percent"] is None
    p.conversion("online", "clip2", stage="waiting")
    assert p.snapshot()["active"][0]["percent"] is None
    p.conversion("online", "clip2", stage="converting",
                 mode="transcode", position_s=20, duration_s=None,
                 encode_speed="0.5x")
    task = p.snapshot()["active"][0]
    assert task["percent"] is None
    assert task["encode_speed"] == "0.5x"
    p.conversion("online", "clip2", stage="converting",
                 position_s=25, duration_s=100)
    assert p.snapshot()["active"][0]["percent"] == 25
    p.finish("online", "clip2", True)
    assert p.snapshot()["active"] == []
    assert p.snapshot()["recent"][0]["percent"] == 100
    clock[0] += p.RECENT_TTL + 1
    assert p.snapshot()["recent"] == []


def test_wrong_length_or_forged_values_do_not_generate_fake_progress():
    p.start("online", "third", "Clip")
    p.download("online", "third", 9000, 4000)
    assert p.snapshot()["active"][0]["bytes_total"] is None
    assert p.snapshot()["active"][0]["percent"] is None
    with pytest.raises(ValueError):
        p.conversion("online", "third", stage="unexpected")


def test_private_progress_endpoint():
    from app import main, auth
    from app.storage import init_data
    init_data()
    auth.set_password("not-a-short-password")
    p.start("online", "id-1", "Test clip", "apple")
    with TestClient(main.app) as client:
        assert client.get("/api/admin/videos/progress").status_code == 401
        login = client.post("/api/auth/login",
                            json={"password": "not-a-short-password"})
        assert login.status_code == 200
        payload = client.get("/api/admin/videos/progress").json()
        assert payload["active"][0]["name"] == "Test clip"
        assert payload["active"][0]["provider"] == "apple"
        assert "remote_url" not in str(payload)


def test_ffmpeg_timestamp_units_and_missing_time():
    assert vc._parse_out_time({"out_time_us": "5000000"}) == 5
    assert vc._parse_out_time({"out_time_ms": "2500000"}) == 2.5
    assert vc._parse_out_time({"out_time": "00:01:02.500"}) == 62.5
    assert vc._parse_out_time({"out_time_us": "N/A"}) is None
    assert vc._parse_out_time({}) is None


def test_ffmpeg_live_progress_reader_and_timeout(monkeypatch):
    class FakeProcess:
        def __init__(self, args, **kwargs):
            self.args = args
            self.stdout = io.StringIO(
                "out_time_ms=2500000\nspeed=1.5x\nprogress=continue\n"
                "out_time_us=5000000\nspeed=2.0x\nprogress=end\n"
            )
        def wait(self, timeout):
            assert timeout == 1
            return 0

    cmds = []
    def launch(args, **kwargs):
        cmds.append(args)
        return FakeProcess(args, **kwargs)
    monkeypatch.setattr(vc.subprocess, "Popen", launch)
    callbacks = []
    vc._run_with_progress(["ffmpeg", "-i", "input", "output.mp4"], 20,
                          lambda **item: callbacks.append(item), 10)
    assert "-progress" in cmds[0] and "pipe:1" in cmds[0]
    assert len(callbacks) == 2
    assert callbacks[0]["position_s"] == 2.5
    assert callbacks[1]["position_s"] == 5
    assert callbacks[-1]["encode_speed"] == "2.0x"


def test_conversion_callback_preserves_single_ffmpeg_slot(monkeypatch, tmp_path):
    from test_video_conversion import AVC
    source = tmp_path / "input.mp4"
    source.write_bytes(b"data")
    out = tmp_path / "output.mp4"
    monkeypatch.setattr(vc.shutil, "which",
                        lambda executable: "/usr/bin/" + executable)
    monkeypatch.setattr(vc, "probe_video", lambda src: AVC)
    monkeypatch.setattr(vc, "probe_duration", lambda src: 30.0)
    received = []
    def fake_ffmpeg(cmd, timeout, callback, duration):
        assert duration == 30
        assert timeout == 1200
        callback(stage="converting", position_s=15, duration_s=30,
                 encode_speed="1x")
        out.write_bytes(bytes.fromhex("000000186674797069736f6d") + b"x" * 3000)
    monkeypatch.setattr(vc, "_run_with_progress", fake_ffmpeg)
    mode = vc.normalize_video(source, out,
                              on_progress=lambda **kw: received.append(kw))
    assert mode == "copy"
    assert [x["stage"] for x in received] == [
        "waiting", "converting", "converting", "finalizing"
    ]
    assert received[2]["position_s"] == 15


@pytest.mark.parametrize("events,stalls", [
    ([(1200, None), (2400, None)], True),
    ([(1200, 1), (2400, 2), (3600, 3), (4800, 4)], False),
    ([(1200, None), (1300, 1), (2500, None), (3700, None)], True),
    ([(1200, 1), (2400, 1), (3600, 1)], True),
])
def test_idle_watchdog_checks_twice_and_resets_on_advance(monkeypatch, events, stalls):
    clock = [0.0]
    monkeypatch.setattr(vc.time, "monotonic", lambda: clock[0])
    reader = []
    class SyncReader:
        def __init__(self, target, **kwargs):
            reader.append(target)
        def start(self):
            pass
        def join(self, **kwargs):
            pass
    monkeypatch.setattr(vc.threading, "Thread", SyncReader)
    class Process:
        def __init__(self):
            self.stdout = io.StringIO("")
            self.events = iter(events)
            self.killed = False
        def wait(self, timeout):
            if self.killed:
                return -9
            event = next(self.events, None)
            if event is None:
                return 0
            clock[0], position = event
            if position is not None:
                self.stdout = io.StringIO(
                    f"out_time_us={position * 1000000}\nprogress=continue\n")
                reader[0]()
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        def kill(self):
            self.killed = True
    process = Process()
    monkeypatch.setattr(vc.subprocess, "Popen", lambda *a, **kw: process)
    if stalls:
        with pytest.raises(TimeoutError, match="нет прогресса"):
            vc._run_with_progress(["ffmpeg", "out.mp4"], 1200, None, None)
    else:
        vc._run_with_progress(["ffmpeg", "out.mp4"], 1200, None, None)
    assert process.killed is stalls
