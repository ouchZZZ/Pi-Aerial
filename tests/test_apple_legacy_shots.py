"""Regression fixtures from the real Pi-Aerial inventory of 2026-11-24."""
import pytest

from app import apple_catalog, apple_reconcile, online, online_library
from test_apple_reconcile import setup_refresh

BASE = "https://sylvan.apple.com/Videos/"
CASES = [
    ("DB_D008_C010", "AC9C09DD-1D97-4013-A09F-B0F5259E64C3", "876D51F4-3D78-4221-8AD2-F9E78C0FD9B9"),
    ("DB_D001_C001", "49790B7C-7D8C-466C-A09E-83E38B6BE87A", "E991AC0C-F272-44D8-88F3-05F44EDFE3AE"),
    ("DB_D001_C005", "802866E6-4AAF-4A69-96EA-C582651391F1", "3FFA2A97-7D28-49EA-AA39-5BC9051B2745"),
    ("DB_D011_C010", "BAF76353-3475-4855-B7E1-CE96CC9BC3A7", "9680B8EB-CE2A-4395-AF41-402801F4D6A6"),
    ("GL_G004_C010", "E4ED0B22-EB81-4D4F-A29E-7E1EA6B6D980", "B8F204CE-6024-49AB-85F9-7CA2F6DCD226"),
    ("HK_B005_C011", "30313BC1-BF20-45EB-A7B1-5A6FFDBD2488", "E99FA658-A59A-4A2D-9F3B-58E7BDC71A9A"),
    ("LW_L001_C006", "6E2FC8AC-832D-46CF-B306-BB2A05030C17", "001C94AE-2BA4-4E77-A202-F7DE60E8B1C8"),
    ("LA_A006_C008", "829E69BA-BB53-4841-A138-4DF0C2A74236", "CE279831-1CA7-4A83-A97B-FF1E20234396"),
    ("LA_A005_C009", "30A2A488-E708-42E7-9A90-B749A407AE1C", "35693AEA-F8C4-4A80-B77D-C94B20A68956"),
    ("LA_A008_C004", "B730433D-1B3B-4B99-9500-A286BF7A9940", "92E48DE9-13A1-4172-B560-29B4668A87EE"),
    ("LA_A011_C003", "A284F0BF-E690-4C13-92E2-4672D93E8DE5", "F5804DD6-5963-40DA-9FA0-39C0C6E6DEF9"),
    ("SE_A016_C009", "391BDF6E-3279-4CE1-9CA5-0F82811452D7", "83C65C90-270C-4490-9C69-F51FE03D7F06"),
]


def sample(shot, legacy_id, modern_id):
    seals = shot.startswith("SE_")
    modern_url = BASE + "comp_" + shot + "_v04_SDR_2K_AVC.mov"
    legacy_url = BASE + shot + ("_SDR_20190717_3m30s_SDR_2K_AVC.mov" if seals
                                else "_2K_SDR_HEVC.mov")
    source = "tvos13" if seals else "tvos11"
    modern = list(apple_catalog.normalize(
        [{"id": modern_id, "shotID": shot, "name": "Modern",
          "url-1080-H264": modern_url}], {}, "tvos26"))[0]
    legacy = list(apple_catalog.normalize(
        [{"id": legacy_id, "name": "Old",
          "url-1080-SDR": legacy_url}], {}, source))[0]
    return modern, legacy


@pytest.mark.parametrize("shot,legacy_id,modern_id", CASES)
def test_actual_legacy_shot_matches(shot, legacy_id, modern_id):
    modern, legacy = sample(shot, legacy_id, modern_id)
    groups = apple_catalog.merge([modern, legacy])
    assert len(groups) == 1
    assert set(groups[0]["apple_ids"]) == {legacy_id, modern_id}
    assert len(groups[0]["versions"]) == 2


def test_distinct_region_or_take_not_merged():
    urls = [BASE + code + "_2K_SDR_HEVC.mov" for code in
            ("DB_D001_C001", "DB_D001_C005", "LA_D001_C001")]
    rows = [{"id": str(i), "name": "Same", "url-1080-SDR": url}
            for i, url in enumerate(urls)]
    assert len(apple_catalog.merge(list(apple_catalog.normalize(rows, {}, "tvos11")))) == 3


def test_explicit_variant_not_replaced_with_inferred_code():
    modern, legacy = sample(*CASES[0])
    modern["shot_ids"] = [CASES[0][0] + "_F01"]
    assert len(apple_catalog.merge([modern, legacy])) == 2


def setup_legacy(monkeypatch, tmp_path):
    setup_refresh(monkeypatch, tmp_path)
    modern, legacy = sample(*CASES[0])
    def published(record):
        return apple_reconcile.reconcile(
            [record], [], [], online.allowed_video_url)[0][0]
    old, alias = published(modern), published(legacy)
    groups = apple_catalog.merge([modern, legacy])
    monkeypatch.setattr(apple_catalog, "inventory", lambda *a, **kw: {
        "items": groups, "counts": {"tvos26": 1, "tvos11": 1},
        "records": 2, "merged": 1, "errors": {}, "snapshots": {}})
    return {"apple": {"items": [old, alias]}}, old, alias


def test_refresh_removes_alias_preserves_canonical_file_and_history(monkeypatch, tmp_path):
    sources, old, alias = setup_legacy(monkeypatch, tmp_path)
    ident, duplicate = old["id"], alias["id"]
    path = tmp_path / (ident + ".mp4")
    path.write_bytes(b"canonical")
    (tmp_path / (ident + ".ready")).write_text(online.CACHE_PROFILE)
    online.atomic_json(online_library.SELECTION_FILE, {"version": 1, "ids": [duplicate]})
    online.atomic_json(online.PROCESSED_FILE, {
        "ids": [ident, duplicate], "details": {ident: {"name": "Modern"},
                                               duplicate: {"name": "Old"}}})
    online._refresh_apple_inventory(sources, 10)
    assert [r["id"] for r in sources["apple"]["items"]] == [ident]
    assert sources["apple"]["scan"]["retired_legacy_duplicates"] == 1
    assert sources["apple"]["scan"]["upgrades"] == 0
    assert online_library.selected_ids() == {ident}
    assert path.read_bytes() == b"canonical"
    history = online.read_json(online.PROCESSED_FILE)
    assert history["ids"] == [ident]
    assert duplicate not in history["details"]
    assert duplicate not in {v["id"] for v in online_library.list_videos()}


@pytest.mark.parametrize("suffix", [".mp4", ".ready", ".part", ".mux.mp4"])
def test_legacy_media_never_deleted_by_migration(monkeypatch, tmp_path, suffix):
    sources, old, alias = setup_legacy(monkeypatch, tmp_path)
    path = tmp_path / (alias["id"] + suffix)
    path.write_bytes(b"keep")
    online._refresh_apple_inventory(sources, 10)
    assert sources["apple"]["scan"]["retired_legacy_duplicates"] == 0
    assert len(sources["apple"]["items"]) == 2
    assert path.read_bytes() == b"keep"


def test_excluded_alias_migrates_to_canonical(monkeypatch, tmp_path):
    sources, old, alias = setup_legacy(monkeypatch, tmp_path)
    online.atomic_json(online_library.EXCLUDED_FILE, {"version": 1, "entries": {
        alias["id"]: alias}})
    online.atomic_json(online_library.SELECTION_FILE, {"version": 1, "ids": [alias["id"]]})
    online._refresh_apple_inventory(sources, 10)
    assert online_library.excluded_keys() == {old["id"]}
    assert not online_library.selected_ids()
