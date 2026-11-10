"""Online video catalogs and bounded, opportunistic on-disk cache.

The display plays sources online. Completed copies of clips that were actually
played can be reused from microSD. No background bulk download is performed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

from .storage import DATA, atomic_json, read_json, settings

SOURCE_NAMES = {
    "apple": "Apple Aerial",
    "nasa": "NASA",
    "pixabay": "Pixabay",
    "pexels": "Pexels",
}
SOURCE_URLS = {
    "apple": "https://sylvan.apple.com/Aerials/2x/entries.json",
    "nasa": "https://images-api.nasa.gov/search",
    "pixabay": "https://pixabay.com/api/videos/",
    "pexels": "https://api.pexels.com/v1/videos/search",
}
CATALOG_FILE = DATA / "online_catalog.json"
CACHE_FOLDER = DATA / "online_cache"
REFRESH_SECONDS = 6 * 3600
API_REFRESH_SECONDS = 24 * 3600
MAX_DOWNLOAD = 550 * 1024 * 1024  # a single background cache file
RESERVE_BYTES = 5 * 1024 ** 3
LOCK = threading.RLock()
DOWNLOAD_SLOT = threading.BoundedSemaphore(1)
ACTIVE: set[str] = set()
CACHE_FAILED_UNTIL: dict[str, float] = {}
APPLE_WARM_TARGET = 4


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
            "pixabay": ("pixabay.com",),
            "pexels": ("pexels.com",),
        }.get(provider, ())
        return any(host == d or host.endswith("." + d) for d in suffix)
    except (ValueError, AttributeError):
        return False


def nasa_https_url(url: str) -> str | None:
    """NASA manifests sometimes publish http links to assets with valid HTTPS.

    Upgrade only NASA-owned hosts; never allow arbitrary HTTP media.
    """
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme == "http" and parsed.username is None
                and parsed.password is None and parsed.port in (None, 80)):
            if parsed.hostname and (
                parsed.hostname.lower() == "nasa.gov"
                or parsed.hostname.lower().endswith(".nasa.gov")
            ):
                parsed = parsed._replace(scheme="https", netloc=parsed.hostname.lower())
                url = urllib.parse.urlunsplit(parsed)
        return url if allowed_video_url(url, "nasa") else None
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


def _key(provider: str) -> str:
    # Use environment or a private, non-public JSON file; never send keys to /api/public.
    variable = "PI_AERIAL_" + provider.upper() + "_API_KEY"
    return (os.getenv(variable) or read_json(DATA / "source_keys.json").get(provider) or "").strip()


def requires_remux(entry: dict) -> bool:
    """MOV is a QuickTime container; Chromium must receive real MP4 bytes."""
    return urllib.parse.urlsplit(entry.get("remote_url", "")).path.lower().endswith(".mov")


def source_status() -> list[dict]:
    cfg = settings()
    raw = read_json(CATALOG_FILE).get("sources", {})
    out = []
    for provider, label in SOURCE_NAMES.items():
        state = raw.get(provider, {})
        requires_key = provider in ("pixabay", "pexels")
        out.append({
            "id": provider, "name": label,
            "enabled": bool(cfg["online_sources"].get(provider, False)),
            "ready": (not requires_key or bool(_key(provider))),
            "requires_key": requires_key,
            "count": len(state.get("items", [])),
            "playable": sum(
                1 for entry in state.get("items", [])
                if not requires_remux(entry) or cached_file(entry.get("id", "")) is not None
            ),
            "error": (("Установите ffmpeg: sudo apt install -y ffmpeg"
                       if shutil.which("ffmpeg") is None else None)
                      if provider == "apple" and cfg["online_sources"].get("apple")
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


def _apple() -> list[dict]:
    raw = _http_json(SOURCE_URLS["apple"])
    entries = raw if isinstance(raw, list) else (raw.get("assets") or raw.get("entries") or [])
    result = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        items = [e] + (e.get("assets", []) if isinstance(e.get("assets"), list) else [])
        selected = None
        for item in items:
            if not isinstance(item, dict):
                continue
            # Chromium on Raspberry Pi should never receive HEVC/HDR from this catalog.
            for key in ("url-1080-H264", "url-1080-h264"):
                url = item.get(key)
                if isinstance(url, str) and allowed_video_url(url, "apple"):
                    selected = url
                    break
            if selected:
                break
        if not selected:
            continue
        source_id = str(e.get("id") or e.get("name") or selected)
        title = str(e.get("name") or e.get("accessibilityLabel") or e.get("id") or "Apple Aerial")
        clip = _clip("apple", source_id, selected, title, "Apple Aerial", "https://www.apple.com/tv/")
        if clip:
            result.append(clip)
    return result[:180]


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


def _pixabay() -> list[dict]:
    key = _key("pixabay")
    if not key:
        raise ValueError("API key не задан")
    query = urllib.parse.urlencode({"key": key, "q": "aerial nature",
                                    "video_type": "film", "per_page": 40,
                                    "safesearch": "true"})
    data = _http_json(SOURCE_URLS["pixabay"] + "?" + query)
    output = []
    for e in data.get("hits", []):
        rendition = e.get("videos", {}).get("medium", {})
        url = rendition.get("url")
        clip = _clip("pixabay", str(e.get("id")), url, "Pixabay · aerial nature",
                     "Pixabay / " + str(e.get("user") or "contributor"),
                     str(e.get("pageURL") or "https://pixabay.com/videos/"))
        if clip:
            output.append(clip)
    return output


def _pexels() -> list[dict]:
    key = _key("pexels")
    if not key:
        raise ValueError("API key не задан")
    query = urllib.parse.urlencode({"query": "aerial landscape", "orientation": "landscape",
                                    "per_page": 40})
    data = _http_json(SOURCE_URLS["pexels"] + "?" + query,
                      headers={"Authorization": key})
    output = []
    for e in data.get("videos", []):
        files = [x for x in e.get("video_files", [])
                 if x.get("file_type") == "video/mp4"
                 and x.get("width") and x.get("height")]
        files.sort(key=lambda x: abs(x["width"] - 1920) + abs(x["height"] - 1080))
        if not files:
            continue
        author = e.get("user") or {}
        clip = _clip("pexels", str(e.get("id")), files[0].get("link"),
                     "Pexels · aerial", "Pexels / " + str(author.get("name", "contributor")),
                     str(e.get("url") or "https://www.pexels.com/videos/"))
        if clip:
            output.append(clip)
    return output


FETCHERS = {"apple": _apple, "nasa": _nasa, "pixabay": _pixabay, "pexels": _pexels}


def refresh_catalog(force=False) -> None:
    """Never drop last successful entries due to a transient API outage."""
    with LOCK:
        current = read_json(CATALOG_FILE)
        sources = dict(current.get("sources", {}))
    cfg = settings()
    changed = False
    for name, fetch in FETCHERS.items():
        if not cfg["online_sources"].get(name, False):
            continue
        state = dict(sources.get(name, {}))
        minimum = API_REFRESH_SECONDS if name in ("pixabay", "pexels") else REFRESH_SECONDS
        last_attempt = float(state.get("attempted") or state.get("updated") or 0)
        # API responses must remain cached at least 24h for Pixabay/Pexels.
        # Even failures wait for the next permitted attempt; avoid API hammering.
        # Explicit refresh may retry Apple/NASA after the operator fixes TLS,
        # but never violates the 24h API-cache policy for Pixabay/Pexels.
        is_manual_retry = force and name in ("apple", "nasa")
        if time.time() - last_attempt < (60 if is_manual_retry else minimum):
            continue
        attempted = time.time()
        try:
            clips = fetch()
            if not clips:
                raise ValueError("Каталог пуст или не содержит подходящих MP4")
            state = {"updated": time.time(), "attempted": attempted,
                     "items": clips, "error": None}
        except Exception as exc:
            state["attempted"] = attempted
            state["error"] = str(exc)[:180]
            print(f"[online] {name}: {exc}", flush=True)
        sources[name] = state
        changed = True
        # Do not wait for slower NASA discovery before starting the first MOV.
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
            # Don't advertise unconverted QuickTime bytes as HTML5 MP4.
            if requires_remux(item) and not cached:
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
    path = CACHE_FOLDER / (clip_id + ".mp4")
    if path.is_file() and not path.is_symlink():
        # Old releases incorrectly stored QuickTime MOV bytes as .mp4.
        # Invalidate them so the new worker can remux into actual MP4.
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[8:12] == b"qt  ":
            path.unlink(missing_ok=True)
            return None
        return path
    return None


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
        if final.exists():
            return
        # Remove incomplete files left by a crashed older download.
        temp.unlink(missing_ok=True)
        muxed.unlink(missing_ok=True)
        provider = entry["provider"]
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
                while True:
                    buf = response.read(1024 * 256)
                    if not buf:
                        break
                    total += len(buf)
                    if total > MAX_DOWNLOAD or shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES:
                        raise ValueError("Достигнут лимит видео или свободного места")
                    file.write(buf)
            if total < 1024:
                raise ValueError("Пустой видеоролик")
            with temp.open("rb") as f:
                header = f.read(12)
            if header[4:8] != b"ftyp":
                raise ValueError("Ответ сервера не содержит MP4/MOV")
            is_mov = urllib.parse.urlsplit(entry["remote_url"]).path.lower().endswith(".mov")
            if is_mov:
                ffmpeg = shutil.which("ffmpeg")
                if not ffmpeg:
                    raise ValueError("Для кеширования MOV нужен ffmpeg; онлайн-поток доступен")
                if shutil.disk_usage(CACHE_FOLDER).free < RESERVE_BYTES + total:
                    raise ValueError("Не хватает места на безопасный ремультиплекс MOV")
                # Copy the H.264 track into a fast-start MP4 container. No
                # re-encoding or extra CPU-heavy transcoding on Raspberry Pi.
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                     "-i", str(temp), "-map", "0:v:0", "-an", "-c:v", "copy",
                     "-movflags", "+faststart", str(muxed)],
                    check=True, timeout=120, capture_output=True,
                )
                with muxed.open("rb") as remuxed:
                    if remuxed.read(12)[4:8] != b"ftyp":
                        raise ValueError("Ошибка при преобразовании MOV в MP4")
                os.replace(muxed, final)
            else:
                os.replace(temp, final)
            _prune(max_bytes, ident)
        finally:
            temp.unlink(missing_ok=True)
            muxed.unlink(missing_ok=True)
    except Exception as exc:
        with LOCK:
            CACHE_FAILED_UNTIL[ident] = time.time() + 3600
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
                or CACHE_FAILED_UNTIL.get(ident, 0) > time.time()):
            return
        if not DOWNLOAD_SLOT.acquire(blocking=False):
            return
        ACTIVE.add(ident)
    threading.Thread(target=_cache_worker, args=(dict(entry),), daemon=True).start()


def warm_cache() -> None:
    """Prepare a small number of MOV videos for immediate HTML5 playback."""
    cfg = settings()
    if not cfg.get("online_cache_enabled", True) or shutil.which("ffmpeg") is None:
        return
    with LOCK:
        catalog = read_json(CATALOG_FILE).get("sources", {})
    for provider, target in (("apple", APPLE_WARM_TARGET), ("nasa", 2)):
        if not cfg.get("online_sources", {}).get(provider, False):
            continue
        items = catalog.get(provider, {}).get("items", [])
        movs = [entry for entry in items if isinstance(entry, dict)
                and requires_remux(entry)
                and allowed_video_url(entry.get("remote_url"), provider)]
        ready = sum(1 for entry in movs if cached_file(entry.get("id", "")))
        if ready >= target:
            continue
        for entry in movs:
            ident = entry.get("id", "")
            if not isinstance(ident, str) or len(ident) != 24:
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
            count += 1
    return count
