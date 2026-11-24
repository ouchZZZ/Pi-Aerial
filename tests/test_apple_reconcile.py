from app import apple_catalog, apple_reconcile, online, online_library

BASE = "https://sylvan.apple.com/Videos/"
LOW = BASE + "scene_SDR_720p.mov"
HIGH = BASE + "scene_SDR_2K_AVC.mov"


def group(ident="scene", url=HIGH, key="url-1080-H264"):
    return list(apple_catalog.normalize(
        [{"id": ident, "name": "City", key: url}], {}, "tvos26"))[0]


def old_clip(url=LOW, provider="apple"):
    return online._clip(provider, "scene", url, "Старое название",
                        "Apple", "https://www.apple.com/tv/")


def test_legacy_id_and_localized_name_survive():
    old = old_clip()
    rows, upgrades, conflicts = apple_reconcile.reconcile(
        [group()], [old], [], online.allowed_video_url)
    assert len(rows) == 1
    assert rows[0]["id"] == old["id"]
    assert rows[0]["name"] == old["name"]
    assert rows[0]["remote_url"] == LOW
    assert upgrades == {old["id"]: HIGH}
    assert conflicts == 0


def test_never_downgrade_or_replace_equal_rank():
    old = old_clip(HIGH)
    rows, upgrades, _ = apple_reconcile.reconcile(
        [group(url=LOW, key="url-720-SDR")], [old], [], online.allowed_video_url)
    assert not upgrades
    assert rows[0]["remote_url"] == HIGH


def test_extras_provider_and_id_survive():
    old = old_clip(provider="apple_extras")
    rows, upgrades, _ = apple_reconcile.reconcile(
        [group()], [], [old], online.allowed_video_url)
    assert len(rows) == 1
    assert rows[0]["id"] == old["id"]
    assert rows[0]["provider"] == "apple_extras"
    assert upgrades == {old["id"]: HIGH}


def test_apple_shot_id_connects_different_urls():
    rows = [
        {"id": "one", "shotID": "HK_H004_C008", "url-1080-H264": HIGH},
        {"id": "two", "shotID": "HK_H004_C008",
         "url-4K-SDR-240FPS": BASE + "different_4k.mov"},
    ]
    records = list(apple_catalog.normalize(rows, {}, "test"))
    assert len(apple_catalog.merge(records)) == 1


def test_conflicting_published_ids_are_not_destroyed():
    rows, upgrades, conflicts = apple_reconcile.reconcile(
        [group()], [old_clip(), old_clip(provider="apple_extras")], [],
        online.allowed_video_url)
    assert len(rows) == 2
    assert not upgrades
    assert conflicts == 1


def setup_refresh(monkeypatch, tmp_path, excluded=False, active=False):
    old = old_clip()
    ident = old["id"]
    monkeypatch.setattr(online, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(online, "PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(online, "CACHE_FOLDER", tmp_path)
    monkeypatch.setattr(online, "ACTIVE", {ident} if active else set())
    monkeypatch.setattr(online, "CACHE_FAILED_UNTIL", {})
    monkeypatch.setattr(online, "CACHE_FAILED_REASONS", {})
    monkeypatch.setattr(online_library, "SELECTION_FILE", tmp_path / "selection.json")
    monkeypatch.setattr(online_library, "EXCLUDED_FILE", tmp_path / "excluded.json")
    monkeypatch.setattr(online_library, "_one_shot_using", lambda _: False)
    monkeypatch.setattr(online, "_apple_extras", lambda: [])
    monkeypatch.setattr(apple_catalog, "inventory", lambda *a, **kw: {
        "items": [group()], "counts": {"tvos26": 1}, "records": 1,
        "merged": 0, "errors": {}, "snapshots": {"tvos26": [group()]}})
    sources = {"apple": {"items": [old]}}
    online.atomic_json(online_library.SELECTION_FILE, {"version": 1, "ids": [ident]})
    online.atomic_json(online.PROCESSED_FILE, {"ids": [ident], "details": {ident: {"name": "Old"}}})
    if excluded:
        online.atomic_json(online_library.EXCLUDED_FILE, {"version": 1, "entries": {ident: old}})
    (tmp_path / (ident + ".mp4")).write_bytes(b"old")
    (tmp_path / (ident + ".ready")).write_text(online.CACHE_PROFILE)
    return sources, ident


def test_upgrade_removes_only_old_media_and_keeps_selection(monkeypatch, tmp_path):
    sources, ident = setup_refresh(monkeypatch, tmp_path)
    online._refresh_apple_inventory(sources, 10)
    assert sources["apple"]["items"][0]["remote_url"] == HIGH
    assert sources["apple"]["scan"]["upgrades"] == 1
    assert not (tmp_path / (ident + ".mp4")).exists()
    assert not (tmp_path / (ident + ".ready")).exists()
    assert ident in online_library.selected_ids()
    assert ident not in online.read_json(online.PROCESSED_FILE)["ids"]
    assert ident in online.read_json(online.PROCESSED_FILE)["details"]


def test_excluded_video_not_replaced(monkeypatch, tmp_path):
    sources, ident = setup_refresh(monkeypatch, tmp_path, excluded=True)
    online._refresh_apple_inventory(sources, 10)
    assert sources["apple"]["items"][0]["remote_url"] == LOW
    assert sources["apple"]["scan"]["upgrades"] == 0
    assert ident in online_library.excluded_keys()


def test_active_download_defers_upgrade(monkeypatch, tmp_path):
    sources, ident = setup_refresh(monkeypatch, tmp_path, active=True)
    online._refresh_apple_inventory(sources, 10)
    assert sources["apple"]["items"][0]["remote_url"] == LOW
    assert sources["apple"]["scan"]["pending_upgrades"] == 1
    assert (tmp_path / (ident + ".mp4")).exists()
