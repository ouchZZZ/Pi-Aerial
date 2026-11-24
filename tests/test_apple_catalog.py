import io
import json
import plistlib
import tarfile
from unittest.mock import patch

from app import apple_catalog as catalog

BASE = "https://sylvan.apple.com/Videos/"
A = BASE + "scene_SDR_2K_AVC.mov"
B = BASE + "scene_SDR_4K_HEVC.mov"
C = BASE + "other_SDR_2K_AVC.mov"


def row(ident, url, key="url-1080-H264"):
    return {"id": ident, "name": "Same city", key: url}


def records(rows, source="test"):
    return list(catalog.normalize(rows, {}, source))


def test_same_id_retains_versions_and_prefers_avc():
    result = catalog.merge(records([row("one", B, "url-4K-SDR"), row("one", A)]))
    assert len(result) == 1
    assert result[0]["urls"] == [A, B]


def test_different_scenes_with_same_title_are_distinct():
    assert len(catalog.merge(records([row("one", A), row("two", C)]))) == 2


def test_transitive_url_and_id_matches():
    result = catalog.merge(records([row("one", A), row("two", C),
                                    row("two", A)]))
    assert len(result) == 1
    assert set(result[0]["apple_ids"]) == {"one", "two"}


def test_legacy_batches_and_https():
    raw = [{"assets": [{"id": "one", "url": A.replace("https:", "http:")}]}]
    assert records(raw)[0]["versions"][A] == 5


def test_untrusted_media_ignored():
    assert not records([row("one", "https://sylvan.apple.com.evil.test/file.mov")])


def make_tar(entries, strings=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        files = {"entries.json": json.dumps({"assets": entries}).encode()}
        if strings:
            files["ru.lproj/Localizable.nocache.strings"] = plistlib.dumps(strings)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def test_plist_tar_and_localization():
    target = catalog.BASE + "example.tar"
    config = "https://configuration.apple.com/example.plist"
    entries = [{**row("one", A), "localizedNameKey": "NAME",
                "pointsOfInterest": {"5": "POI"}}]
    data = {config: plistlib.dumps({"resource": target}),
            target: make_tar(entries, {"NAME": "Город", "POI": "Мост"})}
    raw, strings = catalog.load_feed(config, data.__getitem__)
    item = list(catalog.normalize(raw, strings, "mac"))[0]
    assert item["name"] == "Город"
    assert item["captions"] == {"5": "Мост"}


def test_failed_feed_retains_snapshot():
    good = catalog.BASE + "good.json"
    bad = catalog.BASE + "bad.json"
    previous = {"bad": records([row("two", C)], "bad")}

    def fetch(url):
        if url == good:
            return json.dumps([row("one", A)]).encode()
        raise OSError("offline")

    with patch.object(catalog, "FEEDS", {"good": good, "bad": bad}):
        result = catalog.inventory(previous, fetch)
    assert result["unique"] == 2
    assert result["errors"] == {"bad": "offline"}
    assert result["snapshots"]["bad"] == previous["bad"]
