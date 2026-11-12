"""Online catalogs, sequential preparation and durable processed history.

Apple, NASA and Robin Fourcade videos share one sequential preparation pipeline.
Compatible H.264 is remuxed without lossy re-encoding.
At the configured cache limit the worker pauses without evicting ready videos.
"""
from __future__ import annotations

from . import update_control

import hashlib
import html
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import ssl
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

from .storage import DATA, atomic_json, read_json, settings, max_online_cache_gb
from .video_conversion import normalize_video
from . import video_progress

ARCHIVE_THEMES = {
    "ia_nature": ("Internet Archive · Природа",
                  '(title:(nature OR landscape OR mountain OR forest OR ocean) '
                  'OR subject:(nature OR landscape OR mountain OR forest OR ocean))'),
    "ia_cities": ("Internet Archive · Города",
                  '(title:(aerial city OR skyline OR cityscape) '
                  'OR subject:(cityscape OR aerial city OR skyline))'),
    "ia_history": ("Internet Archive · Исторические города",
                   '(title:(historic city OR historical city OR city streets) '
                   'OR subject:(urban history OR city streets OR historic city))'),
    "ia_space": ("Internet Archive · Космос",
                 '(title:(earth from space OR space timelapse OR orbit) '
                 'OR subject:(earth from space OR orbital footage))'),
    "ia_timelapse": ("Internet Archive · Таймлапсы",
                     '(title:(timelapse OR time-lapse OR aurora) '
                     'OR subject:(time-lapse OR timelapse OR aurora))'),
}
# A few distinct searches per topic work much better than a single popular page.
# Terms are trusted constants, never user-supplied query fragments.
ARCHIVE_SEARCHES = {
    "ia_nature": (
        'title:(landscape OR scenery OR wilderness OR mountains OR waterfall OR coastline)',
        'subject:(nature OR landscape OR aerial OR drone OR mountains OR ocean)',
        '(nature OR wilderness OR forest OR river) AND (aerial OR drone OR timelapse)',
    ),
    "ia_cities": (
        'title:(skyline OR cityscape OR aerial OR panorama OR city)',
        'subject:(architecture OR skyline OR cityscape OR urban OR aerial)',
        '(drone OR aerial OR panorama) AND (city OR buildings OR urban)',
    ),
    "ia_history": (
        '(collection:prelinger) AND (city OR street OR skyline OR urban OR aerial)',
        '(historic OR historical OR vintage OR archival) AND (city OR streets OR skyline)',
        'title:(New York OR London OR Paris OR Chicago OR Moscow OR Tokyo)',
    ),
    "ia_space": (
        'title:(earth OR orbital OR astronaut OR ISS OR satellite)',
        'subject:(earth OR space OR astronaut OR astronomy OR orbit)',
        '(space OR orbit OR NASA) AND (timelapse OR earth OR footage)',
    ),
    "ia_timelapse": (
        'title:(timelapse OR time-lapse OR aurora OR clouds OR sunset)',
        'subject:(timelapse OR time-lapse OR aurora OR night-sky)',
        '(stars OR coastline OR mountain OR city) AND (timelapse OR time-lapse)',
    ),
}
SOURCE_NAMES = {
    "apple": "Apple Aerial", "nasa": "NASA", "robin": "Robin Fourcade",
    **{key: label for key, (label, _) in ARCHIVE_THEMES.items()},
}
SOURCE_URLS = {
    "apple": ("https://sylvan.apple.com/itunes-assets/Aerials126/v4/"
              "c0/45/d9/c045d9d0-9606-1535-62fe-189edb4f79eb/"
              "resources-atv-23J-2.tar"),
    "nasa": "https://images-api.nasa.gov/search",
    "robin": "https://raw.githubusercontent.com/RobinFrcd/AerialShots/master/entries.json",
}
CATALOG_FILE = DATA / "online_catalog.json"
PROCESSED_FILE = DATA / "online_processed.json"
SCHEDULER_FILE = DATA / "online_scheduler.json"
CACHE_FOLDER = DATA / "online_cache"
# Round-robin order; the next turn survives application restarts.
SOURCE_ORDER = ("robin", "apple", "nasa", *ARCHIVE_THEMES)
REFRESH_SECONDS = 6 * 3600
RESERVE_BYTES = 5 * 1024 ** 3
LOCK = threading.RLock()
DOWNLOAD_SLOT = threading.BoundedSemaphore(1)
ACTIVE: set[str] = set()
CACHE_WORKERS: dict[threading.Thread, threading.Event | None] = {}
CANCEL_EVENTS: dict[str, threading.Event] = {}

CACHE_FAILED_UNTIL: dict[str, float] = {}
CACHE_FAILED_REASONS: dict[str, str] = {}
# Bounded diagnostic report for the most recent Archive discovery cycle.
ARCHIVE_SCAN_REPORT: dict[str, dict] = {}
# This marker describes playback compatibility, not CRF or encoder settings.
# Keep it stable so a quality update never invalidates the existing cache.
CACHE_PROFILE = "h264-1080p30-8bit-v1"


def allowed_video_url(url: str, provider: str) -> bool:
    """Never fetch a URL or redirect to an unapproved host (SSRF protection)."""
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443):
            return False
        suffix = {
            "apple": ("apple.com", "mzstatic.com"),
            "nasa": ("nasa.gov",),
        }.get(provider, ())
        if provider in ARCHIVE_THEMES:
            # Archive downloads redirect to numbered archive.org storage hosts.
            # Never follow arbitrary hostnames or item metadata URLs.
            if not (host == "archive.org" or
                    re.fullmatch(r"ia[0-9]+\.us\.archive\.org", host)):
                return False
            parts = urllib.parse.unquote(parsed.path).split("/")
            if len(parts) < 4:
                return False
            # Canonical /download/id/file redirects to /N/items/id/file
            # on an archive-owned storage node.
            if parts[1] == "download":
                identifier, filename = parts[2], parts[3:]
            elif (host != "archive.org" and len(parts) >= 5
                  and re.fullmatch(r"[0-9]{1,6}", parts[1])
                  and parts[2] == "items"):
                identifier, filename = parts[3], parts[4:]
            else:
                return False
            return (re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", identifier) is not None
                    and bool(filename)
                    and all(part not in ("", ".", "..") and "\\" not in part
                            for part in filename))
        if provider == "robin":
            # GitHub release downloads redirect to signed GitHub asset CDN URLs.
            # Never accept an arbitrary repository or third-party media host.
            return (
                host == "github.com" and parsed.path.startswith(
                    "/RobinFrcd/AerialShots/releases/download/")
                or host in ("release-assets.githubusercontent.com",
                            "objects.githubusercontent.com")
            )
        return any(host == d or host.endswith("." + d) for d in suffix)
    except (ValueError, AttributeError):
        return False


def nasa_https_url(url: str) -> str | None:
    """Upgrade only NASA-owned HTTP asset links to verified HTTPS."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme == "http" and parsed.username is None
                and parsed.password is None and parsed.port in (None, 80)
                and parsed.hostname and (
                    parsed.hostname.lower() == "nasa.gov"
                    or parsed.hostname.lower().endswith(".nasa.gov")
                )):
            parsed = parsed._replace(scheme="https", netloc=parsed.hostname.lower())
        # NASA sometimes returns raw spaces in media asset paths.
        parsed = parsed._replace(
            path=urllib.parse.quote(parsed.path, safe="/%:._-~")
        )
        normalized = urllib.parse.urlunsplit(parsed)
        return normalized if allowed_video_url(normalized, "nasa") else None
    except ValueError:
        return None


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, provider: str):
        self.provider = provider

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_video_url(newurl, self.provider):
            raise ValueError("Redirect to untrusted video host")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _tls_context() -> ssl.SSLContext:
    """Trust the system store plus current public roots from certifi.

    Verification and hostname checking stay enabled. Do not use unverified contexts.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def open_video_stream(entry: dict, byte_range: str | None = None):
    """Open only catalog-controlled media hosts with verified TLS and bounded timeouts.

    MOV/H.264 from Apple needs a browser-friendly video/mp4 MIME type. The
    caller proxies HTTP Range responses without decoding or transcoding data.
    """
    provider = entry["provider"]
    url = entry["remote_url"]
    if not allowed_video_url(url, provider):
        raise ValueError("Unsafe video URL")
    headers = {"User-Agent": "PiAerial/0.3", "Accept": "*/*"}
    if byte_range is not None:
        import re
        if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", byte_range) or len(byte_range) > 80:
            raise ValueError("Invalid Range")
        headers["Range"] = byte_range
    opener = urllib.request.build_opener(
        SafeRedirect(provider), urllib.request.HTTPSHandler(context=_tls_context())
    )
    return opener.open(urllib.request.Request(url, headers=headers), timeout=16)


def _http_json(url: str, *, headers=None, timeout=9) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "PiAerial/0.3 (local TV)",
                                                "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls_context()) as resp:
            payload = resp.read(4 * 1024 * 1024 + 1)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise ValueError(
                "HTTPS-сертификат не прошёл проверку системных и certifi CA. "
                "Проверьте дату Raspberry Pi, ca-certificates и возможный HTTPS-перехват в сети."
            ) from exc
        raise
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Catalog too large")
    return json.loads(payload)


def requires_remux(entry: dict) -> bool:
    """MOV is a QuickTime container; Chromium must receive real MP4 bytes."""
    return urllib.parse.urlsplit(entry.get("remote_url", "")).path.lower().endswith(".mov")


def source_status() -> list[dict]:
    cfg = settings()
    raw = read_json(CATALOG_FILE).get("sources", {})
    out = []
    for provider, label in SOURCE_NAMES.items():
        state = raw.get(provider, {})
        out.append({
            "id": provider, "name": label,
            "enabled": bool(cfg["online_sources"].get(provider, False)),
            "ready": True,
            "requires_key": False,
            "count": len(state.get("items", [])),
            "playable": sum(
                1 for entry in state.get("items", [])
                if cached_file(entry.get("id", "")) is not None
            ),
            "error": (("Установите ffmpeg: sudo apt install -y ffmpeg"
                       if shutil.which("ffmpeg") is None else None)
                      if provider in SOURCE_NAMES
                      and cfg["online_sources"].get(provider)
                      and not state.get("error") else state.get("error")),
            "last_updated": state.get("updated"),
            "scan": state.get("scan", {}) if provider in ARCHIVE_THEMES else {},
        })
    return out


def _description(*candidates: object) -> str:
    """Keep descriptions from sources as plain text, not fabricated summaries."""
    for value in candidates:
        if isinstance(value, str) and value.strip():
            text = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())
            if text:
                return text[:1200]
    return ""


def _clip(provider: str, source_id: str, url: str, title: str,
          credit: str, page: str, description: str = "") -> dict | None:
    if not allowed_video_url(url, provider):
        return None
    ident = hashlib.sha256((provider + ":" + source_id).encode()).hexdigest()[:24]
    return {"id": ident, "name": title[:110], "provider": provider,
            "remote_url": url, "credit": credit[:150],
            "description": _description(description),
            "page": page if page.startswith("https://") else ""}


def _apple_manifest() -> tuple[dict | list, dict, dict]:
    """Safely parse Apple's tvOS 26 TAR feed without extracting files to disk."""
    req = urllib.request.Request(
        SOURCE_URLS["apple"],
        headers={"User-Agent": "PiAerial/0.3 (local TV)", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=20, context=_tls_context()) as response:
        payload = response.read(12 * 1024 * 1024 + 1)
    if len(payload) > 12 * 1024 * 1024:
        raise ValueError("Apple TAR превышает 12 МБ")

    translations = {}

    def entries_from_tar(data: bytes, nested: bool = False) -> dict | list:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            members = [item for item in archive.getmembers()
                       if item.isfile() and not item.issym() and not item.islnk()]
            for member in members:
                path = Path(member.name)
                language = path.parent.name
                if (path.name != "Localizable.nocache.strings"
                        or language not in ("ru.lproj", "en.lproj")
                        or member.size > 2 * 1024 * 1024):
                    continue
                try:
                    strings = plistlib.loads(archive.extractfile(member).read())
                except (ValueError, OverflowError):
                    continue
                if isinstance(strings, dict):
                    translations[language] = strings
            for member in members:
                if Path(member.name).name == "entries.json":
                    if member.size > 4 * 1024 * 1024:
                        raise ValueError("Apple entries.json слишком большой")
                    file = archive.extractfile(member)
                    if file is not None:
                        return json.load(file)
            if not nested:
                for member in members:
                    if Path(member.name).name == "resources.tar":
                        if member.size > 10 * 1024 * 1024:
                            raise ValueError("Apple resources.tar слишком большой")
                        file = archive.extractfile(member)
                        if file is not None:
                            return entries_from_tar(file.read(), nested=True)
        raise ValueError("Apple TAR не содержит entries.json")

    raw = entries_from_tar(payload)
    return (raw, {**translations.get('en.lproj', {}), **translations.get('ru.lproj', {})},
            translations.get('en.lproj', {}))


def _apple() -> list[dict]:
    manifest = _apple_manifest()
    raw, strings = manifest[:2] if isinstance(manifest, tuple) else (manifest, {})
    english = manifest[2] if isinstance(manifest, tuple) and len(manifest) > 2 else {}
    entries = raw if isinstance(raw, list) else (
        raw.get("assets") or raw.get("entries") or [])
    if not isinstance(entries, list):
        raise ValueError("Неизвестная структура Apple entries.json")
    result = []
    seen: set[str] = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        items = [e] + (e.get("assets", []) if isinstance(e.get("assets"), list) else [])
        selected = None
        # Prefer 1080p SDR (AVC, then HEVC), only fall back to higher resolution.
        # Avoid HDR unless no SDR rendition exists.
        for key in ("url-1080-H264", "url-1080-h264",
                    "url-1080-SDR", "url-4K-SDR",
                    "url-1080-HDR", "url-4K-HDR"):
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = item.get(key)
                if isinstance(url, str) and allowed_video_url(url, "apple"):
                    selected = url
                    break
            if selected:
                break
        if not selected:
            continue
        source_id = str(e.get("id") or e.get("name") or selected)
        if source_id in seen:
            continue
        seen.add(source_id)
        title = str(e.get("name") or e.get("accessibilityLabel")
                    or e.get("id") or "Apple Aerial")
        name_key = e.get("localizedNameKey")
        if isinstance(name_key, str):
            title = _description(strings.get(name_key)) or title
        captions = []
        points = e.get("pointsOfInterest")
        if isinstance(points, dict):
            for second, key in points.items():
                if (not isinstance(second, str) or not re.fullmatch(r"[0-9]{1,6}", second)
                        or not isinstance(key, str)):
                    continue
                caption = _description(strings.get(key))
                if caption:
                    captions.append({"second": int(second), "text": caption})
            captions.sort(key=lambda caption: caption["second"])
        description = _description(
            e.get("description"), e.get("localizedDescription"),
            *(item.get("description") for item in items if isinstance(item, dict)))
        if description.casefold() == title.casefold():
            description = ""
        if not description and captions:
            description = captions[0]["text"]
        clip = _clip("apple", source_id, selected, title,
                     "Apple Aerial", "https://www.apple.com/tv/", description)
        if clip:
            clip["name_en"] = _description(english.get(name_key)) or str(
                e.get("name") or e.get("accessibilityLabel") or source_id)[:110]
            english_captions = [
                (int(second), _description(english.get(key)))
                for second, key in (points.items() if isinstance(points, dict) else [])
                if isinstance(second, str) and re.fullmatch(r"[0-9]{1,6}", second)
                and isinstance(key, str) and _description(english.get(key))
            ]
            english_captions.sort()
            clip["description_en"] = (
                english_captions[0][1] if english_captions else
                _description(e.get("description")))
            if captions:
                clip['captions'] = captions
            result.append(clip)
    return result[:250]


def _nasa_manifest(entry: dict) -> tuple[dict | None, str]:
    """Resolve an actual movie URL: the search endpoint only contains previews."""
    info = (entry.get("data") or [{}])[0]
    sid = str(info.get("nasa_id") or "")
    if not sid or len(sid) > 180 or "/" in sid:
        return None, "bad_id"
    try:
        manifest = _http_json("https://images-api.nasa.gov/asset/" +
                              urllib.parse.quote(sid, safe=""), timeout=8)
    except Exception as exc:
        print(f"[online] nasa asset {sid[:50]}: {exc}", flush=True)
        return None, "manifest_error"
    files = [r.get("href") for r in manifest.get("collection", {}).get("items", [])
             if isinstance(r, dict) and isinstance(r.get("href"), str)]
    movies = [
        secure for url in files
        if isinstance(url, str)
        for secure in [nasa_https_url(url)]
        if secure is not None
        and urllib.parse.urlsplit(secure).path.lower().endswith((".mp4", ".m4v", ".mov"))
    ]
    # Prefer browser-friendly MP4, then medium/small renditions to avoid 4K.
    movies.sort(key=lambda url: (
        not urllib.parse.urlsplit(url).path.lower().endswith((".mp4", ".m4v")),
        not any(t in url.lower() for t in ("~medium", "~small", "_medium", "_small")),
        "~orig" in url.lower(),
        len(url),
    ))
    if not movies:
        return None, "no_movie"
    clip = _clip("nasa", sid, movies[0], str(info.get("title") or "NASA Earth"),
                 "NASA Image and Video Library", "https://images.nasa.gov/details/" +
                 urllib.parse.quote(sid, safe=""),
                 _description(info.get("description"), info.get("description_508")))
    return clip, "ok" if clip else "untrusted"


def _nasa() -> list[dict]:
    # A phrase-only query was too restrictive and produced empty catalogs.
    # Search across several themes and resolve videos from their asset manifests.
    result: list[dict] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for term in ("earth", "international space station", "earth timelapse"):
        query = urllib.parse.urlencode({"q": term, "media_type": "video",
                                        "page_size": 36})
        response = _http_json(SOURCE_URLS["nasa"] + "?" + query, timeout=12)
        entries = response.get("collection", {}).get("items", [])
        candidates = []
        for entry in entries[:24]:
            if not isinstance(entry, dict):
                continue
            info = (entry.get("data") or [{}])[0]
            sid = str(info.get("nasa_id") or "")
            if sid and sid not in seen:
                seen.add(sid)
                candidates.append(entry)
        # Parallel manifests avoid a long delay when one NASA asset times out.
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_nasa_manifest, e) for e in candidates]
            for future in as_completed(futures):
                clip, status = future.result()
                counts[status] = counts.get(status, 0) + 1
                if clip:
                    result.append(clip)
        if len(result) >= 15:
            break
    if not result:
        raise ValueError("NASA: не нашлось видеофайлов после проверки манифестов; " +
                         ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"[online] nasa: {len(result)} videos; " +
          ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), flush=True)
    return result[:40]


def _robin() -> list[dict]:
    """Robin Fourcade's official AerialShots catalog, with 1080p preference.

    Videos are independently published on GitHub Releases. They are downloaded
    only on the local device and go through the existing cache/normalizer.
    """
    manifest = _http_json(SOURCE_URLS["robin"], timeout=15)
    assets = manifest.get("assets") if isinstance(manifest, dict) else None
    if not isinstance(assets, list):
        raise ValueError("Robin Fourcade: invalid entries.json")
    result: list[dict] = []
    seen: set[str] = set()
    # Prefer 1080p AVC, then other 1080p variants; use 4K when HD is absent.
    priorities = ("url-1080-H264", "url-1080-SDR", "url-1080-HDR",
                  "url-4K-SDR", "url-4K-HDR")
    for entry in assets[:250]:
        if not isinstance(entry, dict):
            continue
        source_id = entry.get("id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 160:
            continue
        if source_id in seen:
            continue
        candidates = [
            entry.get(key) for key in priorities
            if isinstance(entry.get(key), str)
            and allowed_video_url(entry[key], "robin")
        ]
        if not candidates:
            continue
        seen.add(source_id)
        title = _description(entry.get("title"), entry.get("name"), source_id)
        points = entry.get("pointsOfInterest")
        details = [
            text for text in (points.values() if isinstance(points, dict) else [])
            if isinstance(text, str)
        ]
        description = _description(entry.get("description"), *details,
                                   entry.get("accessibilityLabel"))
        clip = _clip("robin", source_id, candidates[0], title,
                     "Robin Fourcade (AerialShots)",
                     "https://github.com/RobinFrcd/AerialShots", description)
        if clip:
            clip["name_en"] = title
            clip["description_en"] = description
            result.append(clip)
    return result



def _archive_license(value: object) -> str | None:
    """Allow only an explicit uploader-supplied license permitting conversion.

    These declarations are NOT an independent rights verification.
    """
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str)), "")
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        if (parsed.scheme not in ("http", "https")
                or parsed.hostname != "creativecommons.org"):
            return None
        path = parsed.path.strip("/").lower().split("/")
        if (len(path) >= 2 and path[0] == "licenses"
                and path[1] in ("by", "by-sa")):
            return value.strip()
        if (len(path) >= 2 and path[0] == "publicdomain"
                and path[1] in ("zero", "mark")):
            return value.strip()
    except ValueError:
        pass
    return None


def _archive_resolution(file: dict, metadata: dict) -> tuple[int, int] | None:
    """Accept real recorded dimensions; never treat an unknown 720p as HD."""
    def dimension(value):
        try:
            number = int(value)
            return number if 0 < number < 10000 else 0
        except (ValueError, TypeError):
            return 0
    width = dimension(file.get("width") or file.get("source_pixel_width"))
    height = dimension(file.get("height") or file.get("source_pixel_height"))
    if not width or not height:
        name = str(file.get("name") or "")[:220]
        match = re.search(r"(?<!\d)(\d{3,4})[xX×](\d{3,4})(?!\d)", name)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
    if not width or not height:
        width = dimension(metadata.get("source_pixel_width"))
        height = dimension(metadata.get("source_pixel_height"))
    if _archive_hd_widescreen(width, height):
        return width, height
    return None


def _archive_hd_widescreen(width: int, height: int) -> bool:
    """Real video dimensions must be at least Full HD and genuinely 16:9."""
    return width >= 1920 and height >= 1080 and 1.74 <= width / height <= 1.82


def _archive_candidate(provider: str, doc: dict, payload: dict) -> dict | None:
    """One Internet Archive item becomes at most one suitable HD video."""
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    license_url = _archive_license(metadata.get("licenseurl") or doc.get("licenseurl"))
    if license_url is None:
        return None
    identifier = doc.get("identifier")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", identifier):
        return None
    options = []
    for file in payload.get("files", [])[:250]:
        if not isinstance(file, dict):
            continue
        name = file.get("name")
        if not isinstance(name, str) or len(name) > 240 or (
                not name.lower().endswith((".mp4", ".m4v", ".mov"))):
            continue
        if any(part in ("", ".", "..") for part in name.split("/")):
            continue
        if file.get("private") == "true":
            continue
        resolution = _archive_resolution(file, metadata)
        # Clearly marked SD/720p files need no expensive on-device probe.
        lowered = name.lower()
        if re.search(r"(?:^|[^a-z0-9])(?:360p|480p|576p|720p|sd)(?:[^a-z0-9]|$)", lowered):
            continue
        named_size = re.search(r"(?<!\d)(\d{3,4})[xX×](\d{3,4})(?!\d)", name)
        if named_size and not _archive_hd_widescreen(
                int(named_size.group(1)), int(named_size.group(2))):
            continue
        # A missing width/height is not evidence that a file is 480p.
        # Propose it with an explicit unknown-quality label; the downloaded
        # bytes MUST pass ffprobe before they can enter the playable cache.
        known_size = (file.get("width") or file.get("source_pixel_width")
                      or metadata.get("source_pixel_width"))
        known_height = (file.get("height") or file.get("source_pixel_height")
                        or metadata.get("source_pixel_height"))
        if resolution is None and known_size and known_height:
            continue
        try:
            size = int(file.get("size") or 0)
            duration = float(file.get("length") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if size < 1024 or size > 8 * 1024**3:
            continue
        if duration and not 20 <= duration <= 1800:
            continue
        url = ("https://archive.org/download/" + identifier + "/" +
               urllib.parse.quote(name, safe="/"))
        if not allowed_video_url(url, provider):
            continue
        # Prefer known 1080p quality; unknown remains a review candidate.
        options.append((resolution is None,
                        abs(resolution[1] - 1080) if resolution else 9999,
                        not name.lower().endswith(".mp4"),
                        size, url, resolution, duration))
    if not options:
        return None
    _, _, _, size, url, resolution, duration = min(options)
    quality = f"{resolution[0]}×{resolution[1]}" if resolution else "Неизвестно"
    quality_verified = resolution is not None
    title = _description(doc.get("title"), metadata.get("title"), identifier)
    description = _description(metadata.get("description"),
                               doc.get("description"))
    subject = metadata.get("subject") or doc.get("subject") or ""
    if isinstance(subject, list):
        subject = ", ".join(str(v) for v in subject[:25])
    credit = _description(metadata.get("creator"), doc.get("creator"),
                          "Internet Archive")[:150]
    year = str(metadata.get("year") or doc.get("year") or "")[:12]
    clip = _clip(provider, identifier, url, title, credit,
                 "https://archive.org/details/" + identifier, description)
    if clip:
        clip.update({"name_en": title, "description_en": description,
                     "subject": _description(subject), "year": year,
                     "license": license_url, "quality": quality,
                     "quality_verified": quality_verified,
                     "duration": round(duration) if duration else None,
                     "size_hint": size,
                     "thumbnail": "https://archive.org/services/img/" + identifier})
    return clip


def _archive_theme(provider: str) -> list[dict]:
    """Discover broad but bounded topical proposals without downloading media.

    Rotate search pages and rankings after each successful refresh. Preserve
    existing catalog entries in refresh_catalog, so discovery accumulates.
    """
    catalog = read_json(CATALOG_FILE).get("sources", {})
    previous = catalog.get(provider, {}) if isinstance(catalog, dict) else {}
    cursor = previous.get("scan_cursor", 0) if isinstance(previous, dict) else 0
    cursor = cursor if type(cursor) is int and 0 <= cursor < 10000 else 0
    page = cursor % 5 + 1
    ranking = ("downloads desc" if cursor // 5 % 2 == 0
               else "publicdate desc")
    seen: set[str] = set()
    docs: list[dict] = []
    successful_searches = 0
    errors = []
    for query in ARCHIVE_SEARCHES[provider]:
        params = urllib.parse.urlencode([
            ("q", "mediatype:movies AND (" + query + ")"),
            ("rows", "35"), ("page", str(page)), ("output", "json"),
            ("sort[]", ranking),
            ("fl[]", "identifier"), ("fl[]", "title"),
            ("fl[]", "licenseurl"), ("fl[]", "year"),
            ("fl[]", "creator"), ("fl[]", "subject"),
        ])
        try:
            found = _http_json(
                "https://archive.org/advancedsearch.php?" + params, timeout=18)
            response = found.get("response", {}) if isinstance(found, dict) else {}
            current = response.get("docs", []) if isinstance(response, dict) else []
            if not isinstance(current, list):
                raise ValueError("Internet Archive: invalid search response")
            successful_searches += 1
            for doc in current:
                if not isinstance(doc, dict):
                    continue
                identifier = doc.get("identifier")
                if (not isinstance(identifier, str)
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", identifier)
                        or identifier in seen):
                    continue
                seen.add(identifier)
                docs.append(doc)
                if len(docs) >= 84:
                    break
        except Exception as exc:
            errors.append(str(exc)[:120])
            print(f"[online] archive search {provider}: {exc}", flush=True)
    if not successful_searches:
        raise ValueError("Internet Archive: все поисковые запросы недоступны: " +
                         "; ".join(errors[:2]))
    results: list[dict] = []
    def resolve(doc):
        ident = doc["identifier"]
        try:
            metadata = _http_json("https://archive.org/metadata/" + ident, timeout=10)
            return _archive_candidate(provider, doc, metadata)
        except Exception as exc:
            print(f"[online] archive {provider}/{ident}: {exc}", flush=True)
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        for clip in pool.map(resolve, docs):
            if clip:
                results.append(clip)
    ARCHIVE_SCAN_REPORT[provider] = {
        "scanned": len(docs),
        "accepted": len(results),
        "unverified": sum(clip["quality_verified"] is False for clip in results),
        "queries": successful_searches,
        "page": page, "next_cursor": cursor + 1,
    }
    print(f"[online] archive {provider}: checked={len(docs)}, "
          f"proposals={len(results)}, unverified="
          f"{ARCHIVE_SCAN_REPORT[provider]['unverified']}, page={page}",
          flush=True)
    return results


FETCHERS = {
    "apple": _apple, "nasa": _nasa, "robin": _robin,
    **{provider: (lambda provider=provider: _archive_theme(provider))
       for provider in ARCHIVE_THEMES},
}


def refresh_catalog(force=False, stop: threading.Event | None = None) -> None:
    """Never drop last successful entries due to a transient API outage."""
    with LOCK:
        current = read_json(CATALOG_FILE)
        sources = {name: state for name, state in current.get("sources", {}).items()
                   if name in FETCHERS}
    cfg = settings()
    changed = False
    for name, fetch in FETCHERS.items():
        if stop is not None and stop.is_set():
            break
        if not cfg["online_sources"].get(name, False):
            continue
        state = dict(sources.get(name, {}))
        last_attempt = float(state.get("attempted") or state.get("updated") or 0)
        # Force a first refresh when switching from legacy tvOS 11 to tvOS 26.
        outdated_apple = name == "apple" and state.get("feed") != "tvos26"
        elapsed = time.time() - last_attempt
        if elapsed < (60 if force or outdated_apple else REFRESH_SECONDS):
            continue
        attempted = time.time()
        try:
            clips = fetch()
            if not clips and name not in ARCHIVE_THEMES:
                raise ValueError("Каталог пуст или не содержит подходящих MP4")
            state = {"updated": time.time(), "attempted": attempted,
                     "items": clips, "error": None,
                     "feed": {"apple": "tvos26", "nasa": "nasa", "robin": "aerialshots"}.get(name, name)}
            if name in ARCHIVE_THEMES:
                # Retain previously discovered suggestions across refreshes.
                old = [item for item in sources.get(name, {}).get("items", [])
                       if isinstance(item, dict)]
                combined = {item["id"]: item for item in old if isinstance(item.get("id"), str)}
                combined.update({item["id"]: item for item in clips})
                # Bound catalog growth without dropping explicitly selected items.
                from . import online_library
                selected = online_library.selected_ids()
                keep = [item for item in combined.values() if item["id"] in selected]
                remaining = [item for item in combined.values() if item["id"] not in selected]
                state["items"] = (keep + remaining[-350:])
                report = ARCHIVE_SCAN_REPORT.get(name, {})
                if report:
                    state["scan_cursor"] = report["next_cursor"]
                    state["scan"] = {key: value for key, value in report.items()
                                     if key != "next_cursor"}
        except Exception as exc:
            state["attempted"] = attempted
            state["error"] = str(exc)[:180]
            print(f"[online] {name}: {exc}", flush=True)
        sources[name] = state
        changed = True
        # Make new assets available to the shared sequential worker immediately.
        if name in FETCHERS:
            with LOCK:
                atomic_json(CATALOG_FILE, {"sources": sources})
            if stop is None:
                warm_cache()
            elif not stop.is_set():
                warm_cache(stop=stop)
    if changed:
        with LOCK:
            atomic_json(CATALOG_FILE, {"sources": sources})


def list_clips() -> list[dict]:
    with LOCK:
        sources = read_json(CATALOG_FILE).get("sources", {})
    enabled = settings()["online_sources"]
    from . import online_library
    selected = online_library.selected_ids()
    result = []
    for provider, state in sources.items():
        if not enabled.get(provider, False):
            continue
        for item in state.get("items", []):
            if not isinstance(item, dict) or not allowed_video_url(item.get("remote_url"), provider):
                continue
            item_id = item.get("id", "")
            if not isinstance(item_id, str) or len(item_id) != 24:
                continue
            cached = cached_file(item_id) is not None
            # Online providers can publish QuickTime, HEVC or H.264.
            # Only list files already normalized to our compatible MP4 profile.
            if not cached or item_id not in selected:
                continue
            result.append({"id": item_id, "name": item["name"],
                           "url": "/media/online/" + item_id,
                           "provider": provider, "credit": item.get("credit", ""),
                           "page": item.get("page", ""),
                           "description": item.get("description", ""),
                           "captions": item.get("captions", []),
                           "cached": cached})
    return result


def lookup(clip_id: str) -> dict | None:
    if len(clip_id) != 24 or any(c not in "0123456789abcdef" for c in clip_id):
        return None
    with LOCK:
        entries = read_json(CATALOG_FILE).get("sources", {})
    enabled = settings()["online_sources"]
    from . import online_library
    selected = online_library.selected_ids()
    if clip_id not in selected:
        return None
    for provider, state in entries.items():
        if not enabled.get(provider, False):
            continue
        for entry in state.get("items", []):
            if entry.get("id") == clip_id and allowed_video_url(entry.get("remote_url"), provider):
                return entry
    return None


def cached_file(clip_id: str) -> Path | None:
    """Only serve files normalized and verified by the current converter.

    v0.3.2 and earlier could store raw QuickTime or unsupported H.264 in .mp4.
    The ready marker prevents those legacy files from being reused.
    """
    path = CACHE_FOLDER / (clip_id + ".mp4")
    marker = CACHE_FOLDER / (clip_id + ".ready")
    if path.is_file() and not path.is_symlink():
        if not marker.is_symlink() and marker.is_file():
            if marker.read_text(encoding="utf8") == CACHE_PROFILE:
                return path
        with LOCK:
            if clip_id in ACTIVE:
                # Converter has published the output but not its marker yet.
                return None
        path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
    return None

def _processed_ids() -> set[str]:
    """Journal completed IDs across restarts and cache eviction.

    Import already prepared legacy files before pruning, so existing clips are
    not downloaded again when a new version starts rotating the cache.
    """
    with LOCK:
        saved = read_json(PROCESSED_FILE).get("ids", [])
        done = {value for value in saved
                if isinstance(value, str) and len(value) == 24}
        for marker in CACHE_FOLDER.glob("*.ready"):
            if (marker.is_file() and not marker.is_symlink()
                    and (CACHE_FOLDER / (marker.stem + ".mp4")).is_file()):
                try:
                    if marker.read_text(encoding="utf8") == CACHE_PROFILE:
                        done.add(marker.stem)
                except OSError:
                    continue
        if done != set(saved):
            current = read_json(PROCESSED_FILE)
            current["ids"] = sorted(done)
            atomic_json(PROCESSED_FILE, current)
        return done


def _mark_processed(ident: str) -> None:
    with LOCK:
        done = _processed_ids()
        if ident not in done:
            done.add(ident)
            atomic_json(PROCESSED_FILE, {"ids": sorted(done)})


def _cache_used_bytes() -> int:
    """Only completed online MP4 files count toward the configured budget."""
    try:
        return sum(
            path.stat().st_size for path in CACHE_FOLDER.glob("*.mp4")
            if path.is_file() and not path.is_symlink()
            and not path.name.endswith(".mux.mp4")
        )
    except OSError:
        # A transient storage error must not trigger a destructive cleanup.
        return 0


def cache_status() -> dict:
    """Report safe download admission, excluding temporary converter files."""
    cfg = settings()
    limit = int(cfg.get("online_cache_gb", 12)) * 1024 ** 3
    used = _cache_used_bytes()
    free = None
    try:
        disk_path = CACHE_FOLDER
        while not disk_path.exists() and disk_path != disk_path.parent:
            disk_path = disk_path.parent
        free = shutil.disk_usage(disk_path).free
    except OSError:
        pass
    # Both the source and converted output coexist on disk.
    quota_budget = max(0, limit - used) // 2
    disk_budget = max(0, free - RESERVE_BYTES) // 2 if free is not None else 0
    file_limit = min(quota_budget, disk_budget)
    limit_paused = file_limit < 1024
    # Keep the existing disk reserve; source and output temporarily coexist.
    disk_paused = free is None or free < RESERVE_BYTES + 2048
    return {
        "used_bytes": used,
        "limit_bytes": limit,
        "free_bytes": free,
        "max_limit_gb": max_online_cache_gb(),
        "file_limit_bytes": file_limit,
        "paused": limit_paused or disk_paused,
        "pause_reason": "disk" if disk_paused else ("limit" if limit_paused else None),
    }


def _download_failure_message(exc: Exception) -> str:
    """A short, safe diagnosis for the admin UI, without leaking download URLs."""
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code in (401, 403):
            return f"HTTP {code}: доступ к файлу запрещён сервером"
        if code == 404:
            return "HTTP 404: файл удалён или недоступен на сервере"
        if code == 429:
            return "HTTP 429: Internet Archive ограничил частоту запросов"
        if code in (500, 502, 503, 504):
            return f"HTTP {code}: сервер временно недоступен"
        return f"HTTP {code}: ошибка загрузки"
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", "")).lower()
        if "timed out" in reason or "timeout" in reason:
            return "Превышено время ожидания ответа видеосервера"
        if "certificate" in reason or "ssl" in reason:
            return "Ошибка проверки HTTPS-соединения"
        return "Не удалось подключиться к серверу видео"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "Потеряно соединение с видеосервером"
    if isinstance(exc, ValueError):
        message = str(exc)
        # ValueError is raised only with trusted, local diagnostic wording;
        # any URL inside third-party exception text still must be stripped.
        message = re.sub(r"https?://\S+", "[адрес скрыт]", message)
        return message[:180] or "Недопустимый видеофайл"
    return "Ошибка подготовки: " + type(exc).__name__


class _CombinedStop:
    """A per-video cancellation and the owning application lifespan stop."""
    def __init__(self, parent, own):
        self.parent, self.own = parent, own

    def is_set(self):
        return self.own.is_set() or (self.parent is not None and self.parent.is_set())


def _cache_worker(entry: dict, slot: threading.BoundedSemaphore | None = None,
                  stop: threading.Event | None = None) -> None:
    # Keep the acquired semaphore even if the module global is replaced.
    slot = slot if slot is not None else DOWNLOAD_SLOT
    ident = entry["id"]
    with LOCK:
        own = CANCEL_EVENTS.get(ident, threading.Event())
    combined = _CombinedStop(stop, own)
    with update_control.media_job() as admitted:
        try:
            if not admitted or combined.is_set():
                return
            cfg = settings()
            if not cfg.get("online_cache_enabled", True):
                return
            max_bytes = int(cfg.get("online_cache_gb", 12)) * 1024**3
            CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
            admission = cache_status()
            if admission["paused"]:
                return
            file_limit = admission["file_limit_bytes"]
            temp = CACHE_FOLDER / (ident + ".part")
            muxed = CACHE_FOLDER / (ident + ".mux.mp4")
            final = CACHE_FOLDER / (ident + ".mp4")
            if cached_file(ident) is not None:
                return
            # Remove incomplete files left by a crashed older download.
            temp.unlink(missing_ok=True)
            muxed.unlink(missing_ok=True)
            provider = entry["provider"]
            video_progress.start("online", ident, entry.get("name") or ident, provider)
            opener = urllib.request.build_opener(SafeRedirect(provider),
                                                  urllib.request.HTTPSHandler(context=_tls_context()))
            req = urllib.request.Request(entry["remote_url"],
                                         headers={"User-Agent": "PiAerial/0.3"})
            total = 0
            try:
                with opener.open(req, timeout=18) as response, temp.open("xb") as file:
                    length = int(response.headers.get("Content-Length") or 0)
                    if length > file_limit:
                        raise ValueError("Размер ролика превышает 50% свободной квоты кеша")
                    video_progress.download("online", ident, 0, length or None)
                    while True:
                        if combined.is_set():
                            raise InterruptedError("Фоновая загрузка снята с выбора")
                        buf = response.read(1024 * 256)
                        if not buf:
                            break
                        total += len(buf)
                        if total > file_limit or shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES:
                            raise ValueError("Достигнут лимит видео или свободного места")
                        file.write(buf)
                        video_progress.download("online", ident, total, length or None)
                if total < 1024:
                    raise ValueError("Пустой видеоролик")
                with temp.open("rb") as f:
                    header = f.read(12)
                if header[4:8] != b"ftyp":
                    raise ValueError("Ответ сервера не содержит MP4/MOV")
                # Metadata is incomplete for many Archive items. Real video
                # dimensions are the final, non-negotiable admission gate.
                if provider in ARCHIVE_THEMES:
                    from .video_conversion import probe_video
                    actual = probe_video(temp)
                    width = int(actual.get("width") or 0)
                    height = int(actual.get("height") or 0)
                    if not _archive_hd_widescreen(width, height):
                        raise ValueError(
                            f"Internet Archive: неподходящее качество "
                            f"{width}×{height}; нужно не ниже 1080p и 16:9")
                if shutil.which("ffmpeg") is None:
                    raise ValueError("Нужен ffmpeg для подготовки видео")
                if shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES + total:
                    raise ValueError("Недостаточно места для преобразования видео")
                print(f"[online] preparing {provider}: {ident} "
                      f"({total // 1024 // 1024} МБ исходник)", flush=True)
                kwargs = {"max_bytes": file_limit,
                          "on_progress": lambda **fields: video_progress.conversion(
                              "online", ident, **fields)}
                if stop is not None or ident in CANCEL_EVENTS:
                    kwargs["stop"] = combined
                    if combined.is_set():
                        raise InterruptedError("Фоновая загрузка снята с выбора")
                mode = normalize_video(temp, muxed, **kwargs)
                if combined.is_set():
                    raise InterruptedError("Фоновая загрузка снята с выбора")
                # Other files may be added while we convert. Discard only this
                # new output if it no longer fits: never evict existing MP4.
                if _cache_used_bytes() + muxed.stat().st_size > max_bytes:
                    raise ValueError("Кеш заполнен во время подготовки нового видео")
                from . import online_library
                with LOCK:
                    if combined.is_set() or not online_library.is_selected(ident):
                        raise InterruptedError("Видео снято с выбора перед публикацией")
                    os.replace(muxed, final)
                    (CACHE_FOLDER / (ident + ".ready")).write_text(CACHE_PROFILE, encoding="utf8")
                    _mark_processed(ident)
                    online_library.remember(ident, entry)
                print(f"[online] ready {provider}: {ident} "
                      f"({mode}, {final.stat().st_size // 1024 // 1024} МБ)", flush=True)
                CACHE_FAILED_REASONS.pop(ident, None)
                video_progress.finish("online", ident, True)
            finally:
                temp.unlink(missing_ok=True)
                muxed.unlink(missing_ok=True)
        except InterruptedError:
            # Shutdown is not a failed video; the next start may retry it.
            video_progress.finish("online", ident, False, "Загрузка остановлена")
        except Exception as exc:
            message = _download_failure_message(exc)
            with LOCK:
                CACHE_FAILED_UNTIL[ident] = time.time() + 3600
                CACHE_FAILED_REASONS[ident] = message
            video_progress.finish("online", ident, False, message)
            print(f"[online] cannot cache {entry.get('provider', '')}: "
                  f"{entry.get('name', '')[:85]} ({ident}): {message}; "
                  f"details={exc}", flush=True)
        finally:
            with LOCK:
                ACTIVE.discard(ident)
                CACHE_WORKERS.pop(threading.current_thread(), None)
                CANCEL_EVENTS.pop(ident, None)
            slot.release()


def try_cache(entry: dict, stop: threading.Event | None = None) -> bool:
    """Return True only when a new background download was actually scheduled."""
    if stop is not None and stop.is_set():
        return False
    cfg = settings()
    if (update_control.paused() or not cfg.get("online_autodownload_enabled", True)
            or not cfg.get("online_cache_enabled", True)
            or cache_status()["paused"]):
        return False
    ident = entry["id"]
    from . import online_library
    with LOCK:
        if (not online_library.is_selected(ident)
                or (stop is not None and stop.is_set())
                or ident in ACTIVE or cached_file(ident) is not None
                or CACHE_FAILED_UNTIL.get(ident, 0) > time.time()):
            return False
        slot = DOWNLOAD_SLOT
        if not slot.acquire(blocking=False):
            return False
        ACTIVE.add(ident)
        CANCEL_EVENTS[ident] = threading.Event()
        worker = threading.Thread(target=_cache_worker,
                                  args=(dict(entry), slot, stop), daemon=True)
        CACHE_WORKERS[worker] = stop
    try:
        worker.start()
    except BaseException:
        with LOCK:
            CACHE_WORKERS.pop(worker, None)
            ACTIVE.discard(ident)
            CANCEL_EVENTS.pop(ident, None)
            slot.release()
        raise
    return True


def wait_for_cache_workers(stop: threading.Event, timeout: float = 20) -> None:
    """Join downloads/conversions spawned by this lifespan only."""
    with LOCK:
        workers = [worker for worker, owner in CACHE_WORKERS.items() if owner is stop]
    deadline = time.monotonic() + timeout
    for worker in workers:
        if worker.ident is not None:
            worker.join(timeout=max(0, deadline - time.monotonic()))


def warm_cache(stop: threading.Event | None = None) -> None:
    """Admit one video at a time, rotating providers after each accepted job.

    All sources can fill the configured cache. The round-robin cursor is only
    advanced after try_cache really starts a worker, so an occupied slot,
    pause, failed entry or disabled provider cannot consume another turn.
    """
    if stop is not None and stop.is_set():
        return
    cfg = settings()
    if (not cfg.get("online_autodownload_enabled", True)
            or not cfg.get("online_cache_enabled", True)
            or shutil.which("ffmpeg") is None
            or cache_status()["paused"]):
        return
    from . import online_library
    online_library.cleanup_unselected()
    with LOCK:
        catalog = read_json(CATALOG_FILE).get("sources", {})
        selected = online_library.selected_ids()
        next_source = read_json(SCHEDULER_FILE).get("next")
        start = SOURCE_ORDER.index(next_source) if next_source in SOURCE_ORDER else 0
        order = SOURCE_ORDER[start:] + SOURCE_ORDER[:start]
        for provider in order:
            if stop is not None and stop.is_set():
                return
            if not cfg.get("online_sources", {}).get(provider, False):
                continue
            items = catalog.get(provider, {}).get("items", [])
            if not isinstance(items, list):
                continue
            for entry in items:
                if stop is not None and stop.is_set():
                    return
                if not isinstance(entry, dict) or not allowed_video_url(
                        entry.get("remote_url"), provider):
                    continue
                ident = entry.get("id", "")
                if (not isinstance(ident, str) or len(ident) != 24
                        or ident not in selected or cached_file(ident) is not None
                        or CACHE_FAILED_UNTIL.get(ident, 0) > time.time()):
                    continue
                scheduled = (try_cache(entry) if stop is None
                             else try_cache(entry, stop=stop))
                if scheduled:
                    following = SOURCE_ORDER[
                        (SOURCE_ORDER.index(provider) + 1) % len(SOURCE_ORDER)]
                    atomic_json(SCHEDULER_FILE, {"next": following})
                    return


def clear_cache() -> int:
    """Only completed video cache files are cleared; never local uploaded videos."""
    CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
    count = 0
    for file in CACHE_FOLDER.glob("*.mp4"):
        if file.is_file() and not file.is_symlink() and file.stem not in ACTIVE:
            file.unlink()
            file.with_suffix(".ready").unlink(missing_ok=True)
            count += 1
    return count
