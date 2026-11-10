"""Online video catalogs, sequential preparation and persistent processed history.

Apple videos are downloaded one at a time, transcoded for Raspberry Pi playback,
and never downloaded again after successful preparation, even if the cache evicts
the finished file.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
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

from .storage import DATA, atomic_json, read_json, settings
from .video_conversion import normalize_video
from . import video_progress

SOURCE_NAMES = {"apple": "Apple Aerial", "nasa": "NASA"}
SOURCE_URLS = {
    "apple": ("https://sylvan.apple.com/itunes-assets/Aerials126/v4/"
              "c0/45/d9/c045d9d0-9606-1535-62fe-189edb4f79eb/"
              "resources-atv-23J-2.tar"),
    "nasa": "https://images-api.nasa.gov/search",
}
CATALOG_FILE = DATA / "online_catalog.json"
PROCESSED_FILE = DATA / "online_processed.json"
CACHE_FOLDER = DATA / "online_cache"
REFRESH_SECONDS = 6 * 3600
MAX_DOWNLOAD = 550 * 1024 * 1024  # a single background cache file
RESERVE_BYTES = 5 * 1024 ** 3
LOCK = threading.RLock()
DOWNLOAD_SLOT = threading.BoundedSemaphore(1)
ACTIVE: set[str] = set()
CACHE_FAILED_UNTIL: dict[str, float] = {}
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
                      if provider in ("apple", "nasa")
                      and cfg["online_sources"].get(provider)
                      and not state.get("error") else state.get("error")),
            "last_updated": state.get("updated"),
        })
    return out


def _clip(provider: str, source_id: str, url: str, title: str,
          credit: str, page: str) -> dict | None:
    if not allowed_video_url(url, provider):
        return None
    ident = hashlib.sha256((provider + ":" + source_id).encode()).hexdigest()[:24]
    return {"id": ident, "name": title[:110], "provider": provider,
            "remote_url": url, "credit": credit[:150],
            "page": page if page.startswith("https://") else ""}


def _apple_manifest() -> dict | list:
    """Safely parse Apple's tvOS 26 TAR feed without extracting files to disk."""
    req = urllib.request.Request(
        SOURCE_URLS["apple"],
        headers={"User-Agent": "PiAerial/0.3 (local TV)", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=20, context=_tls_context()) as response:
        payload = response.read(12 * 1024 * 1024 + 1)
    if len(payload) > 12 * 1024 * 1024:
        raise ValueError("Apple TAR превышает 12 МБ")

    def entries_from_tar(data: bytes, nested: bool = False) -> dict | list:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            members = [item for item in archive.getmembers()
                       if item.isfile() and not item.issym() and not item.islnk()]
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

    return entries_from_tar(payload)


def _apple() -> list[dict]:
    raw = _apple_manifest()
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
        clip = _clip("apple", source_id, selected, title,
                     "Apple Aerial", "https://www.apple.com/tv/")
        if clip:
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
                 urllib.parse.quote(sid, safe=""))
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


FETCHERS = {"apple": _apple, "nasa": _nasa}


def refresh_catalog(force=False) -> None:
    """Never drop last successful entries due to a transient API outage."""
    with LOCK:
        current = read_json(CATALOG_FILE)
        sources = {name: state for name, state in current.get("sources", {}).items()
                   if name in FETCHERS}
    cfg = settings()
    changed = False
    for name, fetch in FETCHERS.items():
        if not cfg["online_sources"].get(name, False):
            continue
        state = dict(sources.get(name, {}))
        last_attempt = float(state.get("attempted") or state.get("updated") or 0)
        # Force a first refresh when switching from legacy tvOS 11 to tvOS 26.
        outdated_apple = name == "apple" and state.get("feed") != "tvos26"
        if not outdated_apple and time.time() - last_attempt < (
                60 if force else REFRESH_SECONDS):
            continue
        attempted = time.time()
        try:
            clips = fetch()
            if not clips:
                raise ValueError("Каталог пуст или не содержит подходящих MP4")
            state = {"updated": time.time(), "attempted": attempted,
                     "items": clips, "error": None,
                     "feed": "tvos26" if name == "apple" else "nasa"}
        except Exception as exc:
            state["attempted"] = attempted
            state["error"] = str(exc)[:180]
            print(f"[online] {name}: {exc}", flush=True)
        sources[name] = state
        changed = True
        # Make new Apple assets available to the sequential worker immediately.
        if name in ("apple", "nasa"):
            with LOCK:
                atomic_json(CATALOG_FILE, {"sources": sources})
            warm_cache()
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
            # Apple and NASA publish a mix of QuickTime, HEVC and H.264.
            # Only list files already normalized to our compatible MP4 profile.
            if provider in ("apple", "nasa") and not cached:
                continue
            result.append({"id": item_id, "name": item["name"],
                           "url": "/media/online/" + item_id,
                           "provider": provider, "credit": item.get("credit", ""),
                           "page": item.get("page", ""), "cached": cached})
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
            atomic_json(PROCESSED_FILE, {"ids": sorted(done)})
        return done


def _mark_processed(ident: str) -> None:
    with LOCK:
        done = _processed_ids()
        if ident not in done:
            done.add(ident)
            atomic_json(PROCESSED_FILE, {"ids": sorted(done)})


def _prune(max_bytes: int, except_id: str) -> None:
    candidates = sorted((p for p in CACHE_FOLDER.glob("*.mp4") if p.is_file()
                         and not p.is_symlink() and p.name != except_id + ".mp4"),
                        key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in CACHE_FOLDER.glob("*.mp4")
                if p.is_file() and not p.is_symlink())
    for file in candidates:
        if total <= max_bytes:
            return
        size = file.stat().st_size
        file.unlink(missing_ok=True)
        file.with_suffix(".ready").unlink(missing_ok=True)
        total -= size


def _cache_worker(entry: dict) -> None:
    ident = entry["id"]
    try:
        cfg = settings()
        if not cfg.get("online_cache_enabled", True):
            return
        max_bytes = int(cfg.get("online_cache_gb", 12)) * 1024**3
        CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES + 100 * 1024**2:
            return
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
                if length > MAX_DOWNLOAD:
                    raise ValueError("Слишком большой ролик для кеша")
                video_progress.download("online", ident, 0, length or None)
                while True:
                    buf = response.read(1024 * 256)
                    if not buf:
                        break
                    total += len(buf)
                    if total > MAX_DOWNLOAD or shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES:
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
            mode = normalize_video(
                temp, muxed, force_transcode=True,
                on_progress=lambda **fields: video_progress.conversion(
                    "online", ident, **fields))
            os.replace(muxed, final)
            (CACHE_FOLDER / (ident + ".ready")).write_text(CACHE_PROFILE, encoding="utf8")
            _mark_processed(ident)
            print(f"[online] ready {provider}: {ident} "
                  f"({mode}, {final.stat().st_size // 1024 // 1024} МБ)", flush=True)
            _prune(max_bytes, ident)
            video_progress.finish("online", ident, True)
        finally:
            temp.unlink(missing_ok=True)
            muxed.unlink(missing_ok=True)
    except Exception as exc:
        with LOCK:
            CACHE_FAILED_UNTIL[ident] = time.time() + 3600
        video_progress.finish("online", ident, False, "Ошибка загрузки или обработки")
        print(f"[online] cannot cache {ident}: {exc}", flush=True)
    finally:
        with LOCK:
            ACTIVE.discard(ident)
        DOWNLOAD_SLOT.release()


def try_cache(entry: dict) -> None:
    if not settings().get("online_cache_enabled", True):
        return
    ident = entry["id"]
    with LOCK:
        if (ident in ACTIVE or cached_file(ident) is not None
                or ident in _processed_ids()
                or CACHE_FAILED_UNTIL.get(ident, 0) > time.time()):
            return
        if not DOWNLOAD_SLOT.acquire(blocking=False):
            return
        ACTIVE.add(ident)
    threading.Thread(target=_cache_worker, args=(dict(entry),), daemon=True).start()


def warm_cache() -> None:
    """Schedule at most one new video; the next starts after full conversion."""
    cfg = settings()
    if not cfg.get("online_cache_enabled", True) or shutil.which("ffmpeg") is None:
        return
    with LOCK:
        catalog = read_json(CATALOG_FILE).get("sources", {})
    done = _processed_ids()
    for provider in ("apple", "nasa"):
        if not cfg.get("online_sources", {}).get(provider, False):
            continue
        items = catalog.get(provider, {}).get("items", [])
        candidates = [entry for entry in items if isinstance(entry, dict)
                      and allowed_video_url(entry.get("remote_url"), provider)]
        # NASA retains its small warm set; Apple prepares all unique clips one
        # at a time, with bounded cache and a persistent no-redownload journal.
        if provider == "nasa" and sum(
                cached_file(e.get("id", "")) is not None for e in candidates) >= 2:
            continue
        for entry in candidates:
            ident = entry.get("id", "")
            if not isinstance(ident, str) or len(ident) != 24 or ident in done:
                continue
            if cached_file(ident) or CACHE_FAILED_UNTIL.get(ident, 0) > time.time():
                continue
            try_cache(entry)
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
