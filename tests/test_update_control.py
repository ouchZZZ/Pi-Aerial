"""Updates drain admitted media work and never starve behind the next video."""
import threading

import pytest

from app import github_updates, local_videos, online, update_control, storage
from app.storage import atomic_json, read_json


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(update_control, "DATA", tmp_path)
    monkeypatch.setattr(github_updates, "DATA", tmp_path)
    return tmp_path


def test_update_waits_for_admitted_work_then_blocks_next_job(isolated):
    entered = threading.Event()
    finish = threading.Event()

    def media():
        with update_control.media_job() as admitted:
            assert admitted
            entered.set()
            assert finish.wait(5)

    worker = threading.Thread(target=media)
    worker.start()
    try:
        assert entered.wait(5)
        update_control.request({"source": "github", "revision": "a" * 40})
        assert update_control.paused()
        assert not update_control.ready()
        assert not (isolated / "pending_update.json").exists()
        with update_control.media_job() as admitted:
            assert not admitted
    finally:
        finish.set()
        worker.join(5)
    assert update_control.ready()
    update_control.promote()
    assert read_json(isolated / "pending_update.json")["revision"] == "a" * 40


def test_media_exception_releases_gate_and_duplicate_update_is_rejected(isolated):
    with pytest.raises(RuntimeError):
        with update_control.media_job():
            raise RuntimeError("failed conversion")
    update_control.request({"version": "1.2.3"})
    assert update_control.ready()
    with pytest.raises(ValueError, match="Предыдущее"):
        update_control.request({"version": "1.2.4"})


def test_local_conversion_stays_queued_during_update(isolated, monkeypatch):
    update_control.request({"version": "1.2.3"})
    monkeypatch.setattr(local_videos, "_process_one",
                        lambda: pytest.fail("conversion started while updating"))
    assert local_videos.process_one() is False


def test_online_worker_releases_reserved_slot_when_update_wins_race(isolated):
    ident = "a" * 24
    slot = threading.BoundedSemaphore(1)
    assert slot.acquire(blocking=False)
    online.ACTIVE.add(ident)
    update_control.request({"version": "1.2.3"})
    online._cache_worker({"id": ident}, slot)
    assert ident not in online.ACTIVE
    assert slot.acquire(blocking=False)


def test_old_helper_completion_unpauses_media_even_if_clock_moves_back(isolated):
    atomic_json(isolated / "update_status.json", {"ok": True, "at": 99999})
    update_control.request({"version": "1.2.3"})
    atomic_json(isolated / "update_status.json", {"ok": False, "at": 1})
    (isolated / "pending_update.json").unlink()
    update_control.promote()
    assert not update_control.paused()
    with update_control.media_job() as admitted:
        assert admitted


def test_auto_updates_disabled_by_default_and_require_boolean(isolated, monkeypatch):
    monkeypatch.setattr(storage, "DATA", isolated)
    assert storage.settings()["auto_update_enabled"] is False
    with pytest.raises(ValueError, match="auto_update_enabled"):
        storage.update_settings({"auto_update_enabled": "yes"})


def test_auto_update_checks_hourly_and_skips_installed_revision(isolated, monkeypatch):
    now = [10000]
    calls = []
    monkeypatch.setattr(github_updates.time, "time", lambda: now[0])
    monkeypatch.setattr(github_updates, "settings", lambda: {"auto_update_enabled": True})
    monkeypatch.setattr(github_updates, "remote_revision", lambda: calls.append(1) or "a" * 40)
    monkeypatch.setattr(github_updates, "installed_revision", lambda: "a" * 40)
    github_updates.auto_update_tick()
    github_updates.auto_update_tick()
    assert len(calls) == 1
    assert not update_control.paused()
    now[0] += 3600
    github_updates.auto_update_tick()
    assert len(calls) == 2


def test_auto_update_respects_disabled_setting_and_pending_request(isolated, monkeypatch):
    monkeypatch.setattr(github_updates, "settings", lambda: {"auto_update_enabled": False})
    monkeypatch.setattr(github_updates, "remote_revision",
                        lambda: pytest.fail("unexpected GitHub check"))
    github_updates.auto_update_tick()
    update_control.request({"version": "1.2.3"})
    monkeypatch.setattr(github_updates, "settings", lambda: {"auto_update_enabled": True})
    github_updates.auto_update_tick()


def test_auto_update_queues_new_revision_but_never_retries_failed_sha(isolated, monkeypatch):
    monkeypatch.setattr(github_updates, "settings", lambda: {"auto_update_enabled": True})
    monkeypatch.setattr(github_updates, "remote_revision", lambda: "a" * 40)
    monkeypatch.setattr(github_updates, "installed_revision", lambda: "b" * 40)
    atomic_json(isolated / "update_status.json", {"ok": False, "revision": "a" * 40})
    github_updates.auto_update_tick()
    assert not update_control.paused()
    (isolated / "auto_update_status.json").unlink()
    monkeypatch.setattr(github_updates, "remote_revision", lambda: "c" * 40)
    github_updates.auto_update_tick()
    assert read_json(isolated / "pending_update.json")["revision"] == "c" * 40
    assert read_json(isolated / "pending_update.json")["_automatic"] is True
    assert read_json(isolated / "update_request.json")["automatic"] is True


def test_auto_check_network_error_is_rate_limited_without_pausing_media(isolated, monkeypatch):
    monkeypatch.setattr(github_updates, "settings", lambda: {"auto_update_enabled": True})
    calls = []
    def unavailable():
        calls.append(1)
        raise ValueError("offline")
    monkeypatch.setattr(github_updates, "remote_revision", unavailable)
    github_updates.auto_update_tick()
    github_updates.auto_update_tick()
    assert calls == [1]
    assert read_json(isolated / "auto_update_status.json")["error"] == "offline"
    assert not update_control.paused()


def test_pending_media_request_is_promoted_even_after_auto_updates_disabled(isolated, monkeypatch):
    with update_control.media_job():
        update_control.request({"version": "1.2.3"})
    monkeypatch.setattr(github_updates, "settings", lambda: {"auto_update_enabled": False})
    github_updates.auto_update_tick()
    assert (isolated / "pending_update.json").exists()


@pytest.mark.parametrize("interval", [0, -1, 1.5, True, "60"])
def test_update_interval_rejects_non_positive_or_non_integer(isolated, monkeypatch, interval):
    monkeypatch.setattr(storage, "DATA", isolated)
    with pytest.raises(ValueError, match="Интервал"):
        storage.update_settings({"auto_update_interval_minutes": interval})


def test_update_interval_defaults_to_hour_and_persists_minutes(isolated, monkeypatch):
    monkeypatch.setattr(storage, "DATA", isolated)
    assert storage.settings()["auto_update_interval_minutes"] == 60
    storage.update_settings({"auto_update_interval_minutes": 15})
    assert storage.settings()["auto_update_interval_minutes"] == 15


def test_auto_check_uses_configured_minutes_and_reacts_to_interval_change(isolated, monkeypatch):
    now = [10000]
    config = {"auto_update_enabled": True, "auto_update_interval_minutes": 120}
    calls = []
    monkeypatch.setattr(github_updates.time, "time", lambda: now[0])
    monkeypatch.setattr(github_updates, "settings", lambda: config)
    monkeypatch.setattr(github_updates, "remote_revision", lambda: calls.append(1) or "a" * 40)
    monkeypatch.setattr(github_updates, "installed_revision", lambda: "a" * 40)
    github_updates.auto_update_tick()
    now[0] += 3600
    github_updates.auto_update_tick()
    assert len(calls) == 1
    config["auto_update_interval_minutes"] = 15
    github_updates.auto_update_tick()
    assert len(calls) == 2
    now[0] += 15 * 60 - 1
    github_updates.auto_update_tick()
    assert len(calls) == 2
    now[0] += 1
    github_updates.auto_update_tick()
    assert len(calls) == 3
