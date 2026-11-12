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

SOURCE_NAMES = {"apple": "Apple Aerial", "nasa": "NASA", "robin": "Robin Fourcade"}
SOURCE_URLS = {
    "apple": ("https://sylvan.apple.com/itunes-assets/Aerials126/v4/"
              "c0/45/d9/c045d9d0-9606-1535-62fe-189edb4f79eb/"
              "resources-atv-23J-2.tar"),
    "nasa": "https://images-api.nasa.gov/search",
    "robin": "https://raw.githubusercontent.com/RobinFrcd/AerialShots/master/entries.json",
}
CATALOG_FILE = DATA / "online_catalog.json"
PROCESSED_FILE = DATA / "online_processed.json"
CACHE_FOLDER = DATA / "online_cache"
REFRESH_SECONDS = 6 * 3600
RESERVE_BYTES = 5 * 1024 ** 3
LOCK = threading.RLock()
DOWNLOAD_SLOT = threading.BoundedSemaphore(1)
ACTIVE: set[str] = set()
CACHE_WORKERS: dict[threading.Thread, threading.Event | None] = {}
CACHE_FAILED_UNTIL: dict[str, float] = {}
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
                      if provider in ("apple", "nasa", "robin")
                      and cfg["online_sources"].get(provider)
                      and not state.get("error") else state.get("error")),
            "last_updated": state.get("updated"),
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


FETCHERS = {"apple": _apple, "nasa": _nasa, "robin": _robin}


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
            if not clips:
                raise ValueError("Каталог пуст или не содержит подходящих MP4")
            state = {"updated": time.time(), "attempted": attempted,
                     "items": clips, "error": None,
                     "feed": {"apple": "tvos26", "nasa": "nasa", "robin": "aerialshots"}.get(name, name)}
        except Exception as exc:
            state["attempted"] = attempted
            state["error"] = str(exc)[:180]
            print(f"[online] {name}: {exc}", flush=True)
        sources[name] = state
        changed = True
        # Make new assets available to the shared sequential worker immediately.
        if name in ("apple", "nasa", "robin"):
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
            if provider in ("apple", "nasa", "robin") and not cached:
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


def _cache_worker(entry: dict, slot: threading.BoundedSemaphore | None = None,
                  stop: threading.Event | None = None) -> None:
    # Keep the acquired semaphore even if the module global is replaced.
    slot = slot if slot is not None else DOWNLOAD_SLOT
    ident = entry["id"]
    with update_control.media_job() as admitted:
        try:
            if not admitted or (stop is not None and stop.is_set()):
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
                        if stop is not None and stop.is_set():
                            raise InterruptedError("Фоновая загрузка остановлена")
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
                if shutil.which("ffmpeg") is None:
                    raise ValueError("Нужен ffmpeg для подготовки видео")
                if shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES + total:
                    raise ValueError("Недостаточно места для преобразования видео")
                print(f"[online] preparing {provider}: {ident} "
                      f"({total // 1024 // 1024} МБ исходник)", flush=True)
                kwargs = {"max_bytes": file_limit,
                          "on_progress": lambda **fields: video_progress.conversion(
                              "online", ident, **fields)}
                if stop is not None:
                    kwargs["stop"] = stop
                    if stop.is_set():
                        raise InterruptedError("Фоновая загрузка остановлена")
                mode = normalize_video(temp, muxed, **kwargs)
                if stop is not None and stop.is_set():
                    raise InterruptedError("Фоновая загрузка остановлена")
                # Other files may be added while we convert. Discard only this
                # new output if it no longer fits: never evict existing MP4.
                if _cache_used_bytes() + muxed.stat().st_size > max_bytes:
                    raise ValueError("Кеш заполнен во время подготовки нового видео")
                os.replace(muxed, final)
                (CACHE_FOLDER / (ident + ".ready")).write_text(CACHE_PROFILE, encoding="utf8")
                _mark_processed(ident)
                from . import online_library
                online_library.remember(ident, entry)
                print(f"[online] ready {provider}: {ident} "
                      f"({mode}, {final.stat().st_size // 1024 // 1024} МБ)", flush=True)
                video_progress.finish("online", ident, True)
            finally:
                temp.unlink(missing_ok=True)
                muxed.unlink(missing_ok=True)
        except InterruptedError:
            # Shutdown is not a failed video; the next start may retry it.
            video_progress.finish("online", ident, False, "Загрузка остановлена")
        except Exception as exc:
            with LOCK:
                CACHE_FAILED_UNTIL[ident] = time.time() + 3600
            video_progress.finish("online", ident, False, "Ошибка загрузки или обработки")
            print(f"[online] cannot cache {ident}: {exc}", flush=True)
        finally:
            with LOCK:
                ACTIVE.discard(ident)
                CACHE_WORKERS.pop(threading.current_thread(), None)
            slot.release()


def try_cache(entry: dict, stop: threading.Event | None = None) -> None:
    if stop is not None and stop.is_set():
        return
    cfg = settings()
    if (update_control.paused() or not cfg.get("online_autodownload_enabled", True)
            or not cfg.get("online_cache_enabled", True)
            or cache_status()["paused"]):
        return
    ident = entry["id"]
    with LOCK:
        if ((stop is not None and stop.is_set())
                or ident in ACTIVE or cached_file(ident) is not None
                or ident in _processed_ids()
                or CACHE_FAILED_UNTIL.get(ident, 0) > time.time()):
            return
        slot = DOWNLOAD_SLOT
        if not slot.acquire(blocking=False):
            return
        ACTIVE.add(ident)
        worker = threading.Thread(target=_cache_worker,
                                  args=(dict(entry), slot, stop), daemon=True)
        CACHE_WORKERS[worker] = stop
    try:
        worker.start()
    except BaseException:
        with LOCK:
            CACHE_WORKERS.pop(worker, None)
            ACTIVE.discard(ident)
            slot.release()
        raise


def wait_for_cache_workers(stop: threading.Event, timeout: float = 20) -> None:
    """Join downloads/conversions spawned by this lifespan only."""
    with LOCK:
        workers = [worker for worker, owner in CACHE_WORKERS.items() if owner is stop]
    deadline = time.monotonic() + timeout
    for worker in workers:
        if worker.ident is not None:
            worker.join(timeout=max(0, deadline - time.monotonic()))


def warm_cache(stop: threading.Event | None = None) -> None:
    """Schedule at most one new video; the next starts after full conversion."""
    if stop is not None and stop.is_set():
        return
    cfg = settings()
    if (not cfg.get("online_autodownload_enabled", True)
            or not cfg.get("online_cache_enabled", True)
            or shutil.which("ffmpeg") is None
            or cache_status()["paused"]):
        return
    with LOCK:
        catalog = read_json(CATALOG_FILE).get("sources", {})
    done = _processed_ids()
    for provider in ("robin", "apple", "nasa"):
        if not cfg.get("online_sources", {}).get(provider, False):
            continue
        items = catalog.get(provider, {}).get("items", [])
        candidates = [entry for entry in items if isinstance(entry, dict)
                      and allowed_video_url(entry.get("remote_url"), provider)]
        # NASA retains its small warm set; Robin and Apple prepare unique clips
        # one at a time, with bounded cache and a persistent no-redownload journal.
        if provider == "nasa" and sum(
                cached_file(e.get("id", "")) is not None for e in candidates) >= 2:
            continue
        for entry in candidates:
            if stop is not None and stop.is_set():
                return
            ident = entry.get("id", "")
            if not isinstance(ident, str) or len(ident) != 24 or ident in done:
                continue
            if cached_file(ident) or CACHE_FAILED_UNTIL.get(ident, 0) > time.time():
                continue
            if stop is None:
                try_cache(entry)
            else:
                try_cache(entry, stop=stop)
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
