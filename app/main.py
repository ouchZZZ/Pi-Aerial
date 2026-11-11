"""Pi Aerial: kiosk and locally administered display server."""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import threading
import time
import urllib.error
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import available_timezones
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, bundles, weather, github_updates, online, online_library, local_videos, video_progress, osd_fonts, update_control
from .storage import (BASE, CURRENT_VERSION, CURRENT_REVISION, DATA, WEB, LOCK, atomic_json, init_data,
                      read_json, safe_id, settings, update_settings)

init_data()
STOP = threading.Event()
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_ATTEMPTS_LOCK = threading.Lock()
LOGIN_ATTEMPTS_MAX_IPS = 1000
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.m4v', '.mov', '.mkv'}
DISPLAY_REQUEST_WINDOW = 30
DISPLAY_MEDIA_WINDOW = 24 * 60 * 60
DISPLAY_REQUESTS: dict[str, tuple[float, str, str]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    STOP.clear()
    worker = threading.Thread(target=weather.polling_loop, args=(STOP,), daemon=True)
    worker.start()
    online_worker = threading.Thread(target=_online_loop, args=(STOP,), daemon=True)
    online_worker.start()
    local_video_worker = threading.Thread(target=local_videos.polling_loop, args=(STOP,), daemon=True)
    local_video_worker.start()
    update_worker = threading.Thread(target=github_updates.polling_loop, args=(STOP,), daemon=True)
    update_worker.start()
    yield
    STOP.set()
    worker.join(timeout=1)
    online_worker.join(timeout=1)
    local_video_worker.join(timeout=1)
    update_worker.join(timeout=1)


def _online_loop(stop: threading.Event):
    while not stop.is_set():
        try:
            online.refresh_catalog()
            online.warm_cache()
        except Exception as exc:
            print(f'[online] refresh failed: {exc}', flush=True)
        if stop.wait(30):
            break


app = FastAPI(title='Pi Aerial', version=CURRENT_VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/assets', StaticFiles(directory=WEB), name='assets')


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # The interactive OSM map requires an Origin/Referer on tile requests.
    # Send only the site's origin (never the admin path or credentials).
    response.headers['Referrer-Policy'] = ('strict-origin-when-cross-origin'
                                            if request.url.path == '/admin' else 'no-referrer')
    response.headers.setdefault('Cache-Control', 'no-store' if request.url.path.startswith(('/api/', '/assets/', '/admin')) else 'public, max-age=300')
    if request.url.path.startswith('/admin'):
        response.headers['X-Frame-Options'] = 'DENY'
    return response


@app.get('/', response_class=HTMLResponse)
def screen():
    return (WEB / 'display.html').read_text(encoding='utf8')


@app.get('/admin', response_class=HTMLResponse)
def admin():
    return (WEB / 'admin.html').read_text(encoding='utf8')


@app.get('/api/health')
def health():
    return {'ok': True, 'version': CURRENT_VERSION, 'revision': CURRENT_REVISION,
            'update_ready': update_control.ready()}


def video_list() -> list[dict]:
    result = []
    for path in sorted((DATA / 'videos').iterdir()):
        if local_videos.is_ready(path):
            result.append({'name': path.name, 'url': '/media/' + quote(path.name),
                           'bytes': path.stat().st_size})
    return result


@app.get('/api/public')
def public():
    modules = bundles.list_modules()
    cfg = settings()
    enabled = cfg.get('enabled_modules', {})
    return {
        'version': CURRENT_VERSION, 'settings': cfg,
        'weather': weather.current_weather(),
        'videos': (video_list() if cfg['local_videos_enabled'] else []) + online.list_clips(),
        'modules': [m for m in modules if enabled.get(m['id'], True)],
        'reload_counter': read_json(DATA / 'reload.json').get('counter', 0),
    }


@app.get('/media/{filename}')
def video_file(filename: str):
    return FileResponse(ready_local_video(filename), media_type='video/mp4',
                        headers={'Cache-Control': 'public, max-age=86400'})


def ready_local_video(filename: str) -> Path:
    if Path(filename).name != filename or Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(404)
    path = DATA / 'videos' / filename
    if not local_videos.is_ready(path):
        raise HTTPException(404)
    return path


def ready_display_video(source: str, name: str) -> Path:
    if source == 'local':
        return ready_local_video(name)
    if source == 'online':
        path = online_library.ready_path(name)
        if path is not None:
            return path
    raise HTTPException(404, 'Подготовленный ролик не найден')


@app.post('/api/admin/display/play-once/{source}/{name}')
def request_display_video(source: str, name: str, _: None = Depends(auth.require_admin)):
    ready_display_video(source, name)
    with LOCK:
        now = time.monotonic()
        for token, (created, _, _) in list(DISPLAY_REQUESTS.items()):
            if now - created >= DISPLAY_MEDIA_WINDOW:
                del DISPLAY_REQUESTS[token]
        DISPLAY_REQUESTS[secrets.token_urlsafe(24)] = (now, source, name)
    return {'ok': True}


@app.post('/api/admin/display/play-once/stop')
def stop_display_video(_: None = Depends(auth.require_admin)):
    with LOCK:
        DISPLAY_REQUESTS.clear()
    return {'ok': True}


@app.get('/api/display/play-once')
def display_video_request():
    with LOCK:
        token = next(reversed(DISPLAY_REQUESTS), None)
        if token is None:
            return {'stop': True}
        if time.monotonic() - DISPLAY_REQUESTS[token][0] < DISPLAY_REQUEST_WINDOW:
            return {'id': token, 'url': '/api/display/play-once/' + token}
    return None


@app.get('/api/display/play-once/{token}')
def display_video_file(token: str):
    with LOCK:
        command = DISPLAY_REQUESTS.get(token)
    if command is None or time.monotonic() - command[0] >= DISPLAY_MEDIA_WINDOW:
        raise HTTPException(404)
    _, source, name = command
    return FileResponse(ready_display_video(source, name), media_type='video/mp4')


@app.get('/modules/{module_id}')
def module_content(module_id: str):
    if not safe_id(module_id):
        raise HTTPException(404)
    entry = DATA / 'modules' / module_id / 'current' / 'index.html'
    if not entry.is_file() or entry.is_symlink():
        raise HTTPException(404)
    return HTMLResponse(entry.read_text(encoding='utf8'), headers={
        'Content-Security-Policy': "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'",
        'Cache-Control': 'no-store',
    })





@app.get('/media/online/{clip_id}')
def online_video(clip_id: str, request: Request):
    entry = online.lookup(clip_id)
    if entry is None:
        raise HTTPException(404, 'Онлайн-ролик недоступен или источник выключен')
    path = online.cached_file(clip_id)
    if path:
        path.touch()  # actual playback is the cache LRU access, not the status poll
        return FileResponse(path, media_type='video/mp4',
                            headers={'Cache-Control': 'private, max-age=3600'})
    if online.requires_remux(entry):
        # Never masquerade QuickTime MOV bytes as HTML5 MP4.
        # Start bounded remux preparation; the clip joins the playlist once ready.
        online.try_cache(entry)
        raise HTTPException(503, 'MOV: ролик подготавливается в кеше MP4',
                            headers={'Retry-After': '30'})
    requested_range = request.headers.get('range')
    try:
        upstream = online.open_video_stream(entry, requested_range)
    except ValueError as exc:
        raise HTTPException(416, str(exc)) from exc
    except urllib.error.HTTPError as exc:
        print(f'[video] {entry["provider"]} {clip_id}: HTTP {exc.code}', flush=True)
        raise HTTPException(502, 'Видеосервер вернул ошибку') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f'[video] {entry["provider"]} {clip_id}: {exc}', flush=True)
        raise HTTPException(502, 'Не удалось подключиться к видеосерверу') from exc

    headers = {'Cache-Control': 'no-store', 'Accept-Ranges': 'bytes'}
    for name in ('Content-Length', 'Content-Range'):
        value = upstream.headers.get(name)
        if value:
            headers[name] = value

    # The upstream may label H.264/MOV as video/quicktime, which Chromium
    # rejects. The catalog selects H.264 and we serve it as video/mp4.
    def stream_chunks():
        try:
            while chunk := upstream.read(128 * 1024):
                yield chunk
        except Exception as exc:
            print(f'[video] stream interrupted {clip_id}: {exc}', flush=True)
        finally:
            upstream.close()

    online.try_cache(entry)
    print(f'[video] {entry["provider"]} {clip_id}: upstream {upstream.status}; '
          f'range={requested_range or "none"}; content-type={upstream.headers.get("Content-Type")}',
          flush=True)
    return StreamingResponse(stream_chunks(), status_code=upstream.status,
                             media_type='video/mp4', headers=headers)


@app.get('/api/admin/online/status')
def online_status(_: None = Depends(auth.require_admin)):
    return {'sources': online.source_status(), 'cache_enabled': settings()['online_cache_enabled'],
            'cache_gb': settings()['online_cache_gb'], 'cache': online.cache_status()}


@app.post('/api/admin/online/cache/clear')
def online_clear_cache(_: None = Depends(auth.require_admin)):
    return {'ok': True, 'deleted': online.clear_cache()}


@app.get('/api/admin/online/library')
def online_library_list(_: None = Depends(auth.require_admin)):
    return {'videos': online_library.list_videos()}


@app.get('/api/admin/online/library/{clip_id}/preview')
def online_library_preview(clip_id: str, _: None = Depends(auth.require_admin)):
    path = online_library.ready_path(clip_id)
    if path is None:
        raise HTTPException(404, 'Подготовленный ролик не найден')
    return FileResponse(path, media_type='video/mp4',
                        headers={'Cache-Control': 'private, no-store'})


@app.delete('/api/admin/online/library/{clip_id}')
def online_library_delete(clip_id: str, _: None = Depends(auth.require_admin)):
    try:
        deleted = online_library.delete_video(clip_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, 'Подготовленный ролик не найден')
    return {'ok': True}


@app.post('/api/admin/online/library/{clip_id}/redownload')
def online_library_redownload(clip_id: str, _: None = Depends(auth.require_admin)):
    try:
        updated = online_library.allow_redownload(clip_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not updated:
        raise HTTPException(404, 'Блокировка повторной загрузки не найдена')
    # Normal serial queue resumes on the next scheduler cycle; only one job
    # can start, and only if current cache/disk limits still allow it.
    online.warm_cache()
    return {'ok': True}


@app.post('/api/admin/online/refresh')
async def online_refresh(_: None = Depends(auth.require_admin)):
    # Retry Apple and NASA (with a one-minute minimum between requests).
    await asyncio.to_thread(online.refresh_catalog, True)
    return {'ok': True, 'sources': online.source_status()}


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _prune_login_attempts(now: float) -> None:
    """Discard expired IP attempts. Caller holds LOGIN_ATTEMPTS_LOCK."""
    for address, attempts in list(LOGIN_ATTEMPTS.items()):
        recent = [t for t in attempts if now - t < 300]
        if recent:
            LOGIN_ATTEMPTS[address] = recent
        else:
            del LOGIN_ATTEMPTS[address]


@app.post('/api/auth/login')
def login(form: Login, request: Request):
    ip = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with LOGIN_ATTEMPTS_LOCK:
        _prune_login_attempts(now)
        recent = LOGIN_ATTEMPTS.get(ip, [])
        if len(recent) >= 5:
            raise HTTPException(429, 'Слишком много попыток. Повторите через пять минут.')
        if not auth.check_password(form.password):
            if ip not in LOGIN_ATTEMPTS and len(LOGIN_ATTEMPTS) >= LOGIN_ATTEMPTS_MAX_IPS:
                oldest = min(LOGIN_ATTEMPTS, key=lambda addr: LOGIN_ATTEMPTS[addr][-1])
                del LOGIN_ATTEMPTS[oldest]
            LOGIN_ATTEMPTS[ip] = [*recent, now]
            raise HTTPException(401, 'Неверный пароль')
        LOGIN_ATTEMPTS.pop(ip, None)
    token = auth.token_for()
    response = JSONResponse({'ok': True, 'csrf': auth.csrf_for(token)})
    response.set_cookie('pa_session', token, max_age=auth.DURATION, httponly=True,
                        samesite='strict', secure=request.url.scheme == 'https', path='/')
    return response


@app.get('/api/auth/session')
def auth_session(request: Request, _: None = Depends(auth.require_admin)):
    return {'ok': True, 'csrf': auth.csrf_for(request.cookies['pa_session'])}


@app.post('/api/auth/logout')
def logout(_: None = Depends(auth.require_admin)):
    response = JSONResponse({'ok': True})
    response.delete_cookie('pa_session', path='/')
    return response


class ConfigUpdate(BaseModel):
    changes: dict


@app.get('/api/admin/status')
def admin_status(_: None = Depends(auth.require_admin)):
    disk = shutil.disk_usage(DATA)
    mods = bundles.list_modules()
    cfg = settings()
    return {'version': CURRENT_VERSION, 'config': cfg, 'videos': video_list(),
            'video_processing': local_videos.status(),
            'modules': [{**m, 'enabled': cfg.get('enabled_modules', {}).get(m['id'], True),
                         'rollback_available': (DATA / 'modules' / m['id'] / 'previous').is_dir()}
                        for m in mods],
            'storage': {'total': disk.total, 'used': disk.used, 'free': disk.free},
            'trusted_keys': len(list((DATA / 'trusted_keys').glob('*.pem'))),
            'update_pending': update_control.paused(),
            'update_waiting': ((DATA / 'update_request.json').exists()
                               and not (DATA / 'pending_update.json').exists()),
            'auto_update': read_json(DATA / 'auto_update_status.json'),
            'last_update': read_json(DATA / 'update_status.json'),
            'online_sources': online.source_status(),
            'cache': {'enabled': cfg['online_cache_enabled'],
                      'limit_gb': cfg['online_cache_gb'], **online.cache_status()}}


@app.get('/api/admin/videos/progress')
def admin_video_progress(_: None = Depends(auth.require_admin)):
    """Private, short-lived telemetry. No remote URLs or file paths."""
    return video_progress.snapshot()


@app.get('/api/admin/timezones')
def admin_timezones(_: None = Depends(auth.require_admin)):
    """IANA zones installed on the device; choices match backend validation."""
    zones = sorted(available_timezones())
    return {'timezones': zones or ['UTC', 'Europe/Moscow']}


@app.get('/api/admin/osd/fonts')
def admin_osd_fonts(_: None = Depends(auth.require_admin)):
    """Installed font-family names, not font files or remote webfonts."""
    return {'fonts': osd_fonts.installed_font_families()}


@app.put('/api/admin/config')
def admin_config(body: ConfigUpdate, _: None = Depends(auth.require_admin)):
    try:
        cfg = update_settings(body.changes)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if {'latitude', 'longitude', 'timezone', 'city'} & body.changes.keys():
        threading.Thread(target=_weather_refresh_safely, daemon=True).start()
    if 'online_sources' in body.changes:
        threading.Thread(target=online.refresh_catalog, daemon=True).start()
    return {'ok': True, 'config': cfg}


def _weather_refresh_safely():
    try:
        weather.refresh_weather()
    except Exception as exc:
        print(f'[weather] manual refresh failed: {exc}', flush=True)


class EnabledUpdate(BaseModel):
    enabled: bool


@app.put('/api/admin/modules/{module_id}/enabled')
def module_enabled(module_id: str, data: EnabledUpdate,
                   _: None = Depends(auth.require_admin)):
    if module_id not in {m['id'] for m in bundles.list_modules()}:
        raise HTTPException(404, 'Модуль не найден')
    with LOCK:
        cfg = settings()
        states = dict(cfg.get('enabled_modules', {}))
        states[module_id] = data.enabled
        cfg['enabled_modules'] = states
        atomic_json(DATA / 'settings.json', cfg)
    return {'ok': True}


async def save_upload(upload: UploadFile, maximum: int, suffix: str) -> Path:
    folder = DATA / 'incoming'
    path = folder / (secrets.token_hex(16) + suffix)
    size = 0
    try:
        with path.open('xb') as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError('Файл превышает допустимый размер')
                out.write(chunk)
        if size == 0:
            raise ValueError('Пустой файл')
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@app.post('/api/admin/videos')
async def add_video(file: UploadFile = File(...), _: None = Depends(auth.require_admin)):
    with update_control.media_job() as admitted:
        if not admitted:
            await file.close()
            raise HTTPException(503, 'Обновление ожидает завершения видео; повторите загрузку позже')
        original = Path(file.filename or 'video.mp4').name
        suffix = Path(original).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise HTTPException(422, 'Поддерживаются MP4, WebM, M4V, MOV и MKV')
        # Reject obviously invalid files before enforcing the free-space reserve.
        header = await file.read(12)
        await file.seek(0)
        if suffix in ('.mp4', '.m4v', '.mov') and header[4:8] != b'ftyp':
            raise HTTPException(422, 'Это не MP4/M4V/MOV-файл')
        if suffix in ('.webm', '.mkv') and not header.startswith(b'\x1a\x45\xdf\xa3'):
            raise HTTPException(422, 'Это не WebM/MKV-файл')
        free = shutil.disk_usage(DATA).free
        if free < 9 * 1024**3:
            raise HTTPException(507, 'Нужно 9 ГБ свободного места для загрузки и обработки')
        maximum = min(10 * 1024**3, max(0, free - 9 * 1024**3))
        try:
            incoming = await save_upload(file, maximum, suffix)
            safe_name = re.sub(r'[^0-9A-Za-zА-Яа-яЁё._-]+', '_', original)[:100].strip('._-')
            if not safe_name or not safe_name.lower().endswith(suffix):
                safe_name = 'video' + suffix
            return {'ok': True, 'message': 'Видео принято и поставлено в очередь фоновой подготовки',
                    **local_videos.enqueue(incoming, safe_name)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            if 'incoming' in locals():
                incoming.unlink(missing_ok=True)


@app.delete('/api/admin/videos/{filename}')
def delete_video(filename: str, _: None = Depends(auth.require_admin)):
    if Path(filename).name != filename or Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(404)
    target = DATA / 'videos' / filename
    if target.is_symlink() or not local_videos.is_ready(target):
        raise HTTPException(404)
    target.unlink()
    local_videos.remove_ready(target)
    return {'ok': True}


@app.post('/api/admin/videos/jobs/{job_id}/retry')
def retry_video_job(job_id: str, _: None = Depends(auth.require_admin)):
    if not re.fullmatch(r'[0-9a-f]{16}', job_id) or not local_videos.retry(job_id):
        raise HTTPException(409, 'Задание не найдено или не ожидает повторения')
    return {'ok': True}


@app.delete('/api/admin/videos/jobs/{job_id}')
def delete_video_job(job_id: str, _: None = Depends(auth.require_admin)):
    if not re.fullmatch(r'[0-9a-f]{16}', job_id) or not local_videos.delete_job(job_id):
        raise HTTPException(409, 'Задание не найдено или выполняется')
    return {'ok': True}


@app.post('/api/admin/modules/install')
async def install_module(file: UploadFile = File(...), _: None = Depends(auth.require_admin)):
    try:
        incoming = await save_upload(file, bundles.MAX_BUNDLE_BYTES, '.zip')
        manifest = await asyncio.to_thread(bundles.install_module, incoming)
        return {'ok': True, 'module': manifest}
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        if 'incoming' in locals():
            incoming.unlink(missing_ok=True)


@app.post('/api/admin/modules/{module_id}/rollback')
def module_rollback(module_id: str, _: None = Depends(auth.require_admin)):
    try:
        return {'ok': True, 'module': bundles.rollback_module(module_id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/admin/core/install')
async def install_core(file: UploadFile = File(...), _: None = Depends(auth.require_admin)):
    if update_control.paused():
        raise HTTPException(409, 'Предыдущее обновление ещё выполняется')
    try:
        incoming = await save_upload(file, bundles.MAX_BUNDLE_BYTES, '.zip')
        manifest = await asyncio.to_thread(bundles.stage_core, incoming)
        return {'ok': True, 'version': manifest['version'], 'message': 'Обновление поставлено в очередь с откатом'}
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        if 'incoming' in locals():
            incoming.unlink(missing_ok=True)


@app.post('/api/admin/display/reload')
def reload_screen(_: None = Depends(auth.require_admin)):
    with LOCK:
        old = read_json(DATA / 'reload.json')
        count = int(old.get('counter', 0)) + 1
        atomic_json(DATA / 'reload.json', {'counter': count})
    return {'ok': True, 'counter': count}


@app.get('/api/admin/github/status')
async def github_status(_: None = Depends(auth.require_admin)):
    try:
        return await asyncio.to_thread(github_updates.status)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post('/api/admin/github/update')
async def github_update(_: None = Depends(auth.require_admin)):
    if (DATA / 'pending_update.json').exists():
        raise HTTPException(409, 'Предыдущее обновление ещё выполняется')
    try:
        latest = await asyncio.to_thread(github_updates.remote_revision)
        if latest == github_updates.installed_revision():
            return {'ok': True, 'message': 'Установлена последняя версия', 'revision': latest}
        update_control.request({'source': 'github', 'revision': latest})
        return {'ok': True, 'message': 'Обновление из GitHub поставлено в очередь с откатом', 'revision': latest}
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get('/api/admin/github/modules')
async def github_modules(_: None = Depends(auth.require_admin)):
    try:
        return await asyncio.to_thread(github_updates.catalog)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post('/api/admin/github/modules/{module_id}/install')
async def github_module_install(module_id: str, _: None = Depends(auth.require_admin)):
    try:
        manifest, revision = await asyncio.to_thread(github_updates.install_from_catalog, module_id)
        return {'ok': True, 'module': manifest, 'revision': revision,
                'message': 'Модуль установлен из GitHub'}
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
