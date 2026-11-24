"""Read-only inventory of Apple Aerial manifests.

No cache files or user selections are modified by this module.
"""
from __future__ import annotations

import io
import json
import plistlib
import re
import tarfile
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

BASE = "https://sylvan.apple.com/Aerials/"
MAC = ("https://sylvan.apple.com/itunes-assets/Aerials126/v4/"
       "82/2e/34/822e344c-f5d2-878c-3d56-508d5b09ed61/")
FEEDS = {
    "tvos26": ("https://sylvan.apple.com/itunes-assets/Aerials126/v4/"
               "c0/45/d9/c045d9d0-9606-1535-62fe-189edb4f79eb/resources-atv-23J-2.tar"),
    "macos26": "https://configuration.apple.com/configurations/internetservices/aerials/resources-config-26-0.plist",
    "macos26.4.1": MAC + "resources-26-4-1.tar",
    "tvos16": BASE + "resources-16.tar",
    "tvos15": BASE + "resources-15.tar",
    "tvos13": BASE + "resources-13.tar",
    "tvos12": BASE + "resources.tar",
    "tvos11": BASE + "2x/entries.json",
    "tvos10": "https://a1.phobos.apple.com/us/r1000/000/Features/atv/AutumnResources/videos/entries.json",
    "macos15": MAC + "resources-15-0-2.tar",
    "macos14": MAC + "resources-14-0.tar",
    "macos14.0.10": MAC + "resources-14-0-10.tar",
    "macos14.1": MAC + "resources-14-1-0.tar",
}
MAX_BYTES = 24 * 1024 * 1024
HOSTS = {"sylvan.apple.com", "configuration.apple.com", "a1.phobos.apple.com",
         "a2.phobos.apple.com", "a3.phobos.apple.com", "a4.phobos.apple.com",
         "a5.phobos.apple.com"}

def safe_url(value):
    if not isinstance(value, str):
        return False
    try:
        p = urllib.parse.urlsplit(value)
        return (p.scheme == "https" and p.hostname in HOSTS and
                p.port in (None, 443) and not p.username and not p.password)
    except ValueError:
        return False


class AppleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_url(newurl):
            raise ValueError("Unexpected Apple catalog redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url):
    if not safe_url(url):
        raise ValueError("Unexpected Apple catalog URL")
    opener = urllib.request.build_opener(AppleRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "PiAerial catalog"})
    with opener.open(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Apple catalog too large")
    return data


def _tar_payload(data, depth=0):
    entries, translations, nested = None, {}, None
    # No filesystem extraction; bound the decompressed data and member count.
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r|*") as archive:
        for index, member in enumerate(archive):
            total += member.size
            if index > 6000 or total > 4 * MAX_BYTES:
                raise ValueError("Apple archive exceeds limits")
            if not member.isfile() or member.size > MAX_BYTES:
                continue
            path = PurePosixPath(member.name)
            if path.name == "entries.json":
                entries = json.load(archive.extractfile(member))
            elif path.name == "resources.tar" and depth == 0:
                nested = archive.extractfile(member).read()
            elif path.name in ("Localizable.nocache.strings", "Localizable.nocache.loctable"):
                try:
                    table = plistlib.loads(archive.extractfile(member).read())
                except (ValueError, OverflowError):
                    continue
                if not isinstance(table, dict):
                    continue
                if path.name.endswith(".loctable"):
                    for lang in ("en", "ru"):
                        if isinstance(table.get(lang), dict):
                            translations.setdefault(lang, {}).update(table[lang])
                elif path.parent.name in ("en.lproj", "ru.lproj"):
                    translations.setdefault(path.parent.name[:2], {}).update(table)
    if entries is None and nested is not None:
        entries, more = _tar_payload(nested, depth + 1)
        for lang, values in more.items():
            translations.setdefault(lang, {}).update(values)
    if entries is None:
        raise ValueError("Missing entries.json")
    return entries, translations


def _plist_urls(value):
    if isinstance(value, str):
        if safe_url(value) and urllib.parse.urlsplit(value).path.endswith(".tar"):
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _plist_urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _plist_urls(child)


def load_feed(url, fetch=download):
    data = fetch(url)
    path = urllib.parse.urlsplit(url).path
    if path.endswith(".plist"):
        targets = sorted(set(_plist_urls(plistlib.loads(data))))
        if len(targets) != 1:
            raise ValueError("Ambiguous Apple resource manifest")
        return load_feed(targets[0], fetch)
    if path.endswith(".json"):
        return json.loads(data), {}
    return _tar_payload(data)


def media_url(value):
    if isinstance(value, str) and value.startswith("http://"):
        value = "https://" + value[7:]
    if safe_url(value) and urllib.parse.urlsplit(value).path.lower().endswith((".mov", ".mp4", ".m4v")):
        return value
    return None


def quality(key, url):
    """Compatibility order, not a claim about measured visual fidelity."""
    k = key.lower()
    s = urllib.parse.urlsplit(url).path.lower()
    if "hdr" in k or re.search(r"(?<![a-z])hdr(?![a-z])", s):
        return 1
    if "720" in k or "720p" in s:
        return 0
    if "1080" in k or "2k" in k or re.search(r"(?:1080p|2k_avc|2k_hevc)", s):
        return 5 if ("h264" in k or "avc" in k or "avc" in s) else 4
    if "4k" in k or "4k" in s or "uhd" in s:
        return 3
    return 2


def normalize(raw, translations, source):
    entries = raw if isinstance(raw, list) else raw.get("assets", raw.get("entries", []))
    if not isinstance(entries, list):
        raise ValueError("Invalid Apple asset list")
    # tvOS 10 uses batches with nested assets; later feeds use flat assets.
    queue = list(entries)
    for entry in queue:
        if not isinstance(entry, dict):
            continue
        children = entry.get("assets")
        if isinstance(children, list) and not entry.get("id"):
            queue.extend(children)
            continue
        versions = {}
        for obj in [entry] + (children if isinstance(children, list) else []):
            if not isinstance(obj, dict):
                continue
            for key, value in obj.items():
                if key != "url" and not key.startswith("url-"):
                    continue
                url = media_url(value)
                if url:
                    versions[url] = max(versions.get(url, 0), quality(key, url))
        if not versions:
            continue
        ident = entry.get("id")
        ident = ident if isinstance(ident, str) and ident else ""
        ru, en = translations.get("ru", {}), translations.get("en", {})
        name_key = entry.get("localizedNameKey")
        name = entry.get("name") or entry.get("accessibilityLabel") or ident
        captions = {}
        points = entry.get("pointsOfInterest")
        if isinstance(points, dict):
            for sec, key in points.items():
                if isinstance(key, str) and str(sec).isdigit():
                    caption = ru.get(key) or en.get(key)
                    if isinstance(caption, str) and caption:
                        captions[str(sec)] = caption
        yield {
            "apple_ids": [ident] if ident else [],
            "shot_ids": [entry["shotID"]] if isinstance(entry.get("shotID"), str)
                         and entry["shotID"].strip() else [],
            "sources": [source], "versions": versions,
            "name": ru.get(name_key) or en.get(name_key) or name,
            "name_en": en.get(name_key) or name,
            "captions": captions,
        }


def _identity_keys(record):
    # IDs and complete media URLs are strong evidence. Do NOT merge by place,
    # title, approximate filename, duration or resolution alone.
    # A complete production filename (without only the rendition suffix) is
    # strong evidence across Apple CDN paths. Retain edit/version tokens.
    names = set()
    for url in record["versions"]:
        stem = PurePosixPath(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).stem.lower()
        match = re.fullmatch(r"(.+?)_(?:sdr|hdr)_(?:2k|4k)_(?:avc|hevc)", stem)
        if match and re.search(r"[a-z]\d{3}_c\d{3}", match.group(1)):
            names.add(("master", match.group(1)))
    return (names | {("shot", x.casefold()) for x in record.get("shot_ids", [])} |
            {("id", x.casefold()) for x in record["apple_ids"]} |
            {("url", urllib.parse.urlsplit(u).netloc.lower() +
              urllib.parse.urlsplit(u).path) for u in record["versions"]})


def merge(records):
    """Union transitive strong matches, retaining the first record's metadata."""
    groups = []
    for original in records:
        item = {**original, "apple_ids": list(original["apple_ids"]),
                "sources": list(original["sources"]),
                "shot_ids": list(original.get("shot_ids", [])),
                "versions": dict(original["versions"]),
                "captions": dict(original["captions"])}
        keys = _identity_keys(item)
        matches = [i for i, group in enumerate(groups) if keys & _identity_keys(group)]
        if not matches:
            groups.append(item)
            continue
        target = groups[matches[0]]
        for other in [item] + [groups[i] for i in matches[1:]]:
            for field in ("apple_ids", "sources", "shot_ids"):
                target[field] = list(dict.fromkeys(target[field] + other.get(field, [])))
            for url, rank in other["versions"].items():
                target["versions"][url] = max(rank, target["versions"].get(url, 0))
            for sec, caption in other["captions"].items():
                target["captions"].setdefault(sec, caption)
        for i in reversed(matches[1:]):
            groups.pop(i)
    for item in groups:
        item["urls"] = sorted(item["versions"], key=lambda u: (-item["versions"][u], u))
    return groups


def inventory(previous=None, fetch=download, stop=None):
    """Sequential network requests; retain the last successful feed on failure."""
    previous = previous or {}
    snapshots, errors, counts = {}, {}, {}
    for name, url in FEEDS.items():
        if stop is not None and stop.is_set():
            raise InterruptedError("Apple catalog refresh stopped")
        try:
            raw, translations = load_feed(url, fetch)
            rows = list(normalize(raw, translations, name))
            if not rows:
                raise ValueError("No playable Apple assets")
            snapshots[name] = rows
        except Exception as exc:
            errors[name] = str(exc)[:180]
            if name in previous:
                snapshots[name] = previous[name]
        counts[name] = len(snapshots.get(name, []))
    records = [row for rows in snapshots.values() for row in rows]
    groups = merge(records)
    return {"snapshots": snapshots, "items": groups, "counts": counts,
            "errors": errors, "records": len(records),
            "unique": len(groups), "merged": len(records) - len(groups)}
