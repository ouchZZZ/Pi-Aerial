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
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, bundles, weather, github_updates, online
from .storage import (BASE, CURRENT_VERSION, CURRENT_REVISION, DATA, WEB, LOCK, atomic_json, init_data,
                      read_json, safe_id, settings, update_settings)

init_data()
STOP = threading.Event()
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.m4v'}


@asynccontextmanager
async def lifespan(_: FastAPI):
    STOP.clear()
    worker = threading.Thread(target=weather.polling_loop, args=(STOP,), daemon=True)
    worker.start()
    online_worker = threading.Thread(target=_online_loop, args=(STOP,), daemon=True)
    online_worker.start()
    yield
    STOP.set()
    worker.join(timeout=1)
    online_worker.join(timeout=1)


def _online_loop(stop: threading.Event):
    while not stop.is_set():
        try:
            online.refresh_catalog()
        except Exception as exc:
            print(f'[online] refresh failed: {exc}', flush=True)
        if stop.wait(3600):
            break


app = FastAPI(title='Pi Aerial', version=CURRENT_VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/assets', StaticFiles(directory=WEB), name='assets')


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
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
    return {'ok': True, 'version': CURRENT_VERSION, 'revision': CURRENT_REVISION}


def video_list() -> list[dict]:
    result = []
    for path in sorted((DATA / 'videos').iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
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
    if Path(filename).name != filename or Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(404)
    path = DATA / 'videos' / filename
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404)
    return FileResponse(path, media_type='video/mp4' if path.suffix.lower() != '.webm' else 'video/webm',
                        headers={'Cache-Control': 'public, max-age=86400'})


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
        return FileResponse(path, media_type='video/mp4',
                            headers={'Cache-Control': 'private, max-age=3600'})
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
            'cache_gb': settings()['online_cache_gb']}


@app.post('/api/admin/online/cache/clear')
def online_clear_cache(_: None = Depends(auth.require_admin)):
    return {'ok': True, 'deleted': online.clear_cache()}


@app.post('/api/admin/online/refresh')
async def online_refresh(_: None = Depends(auth.require_admin)):
    # Only Apple and NASA may be retried early; commercial APIs retain 24h TTL.
    await asyncio.to_thread(online.refresh_catalog, True)
    return {'ok': True, 'sources': online.source_status()}


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@app.post('/api/auth/login')
def login(form: Login, request: Request):
    ip = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    recent = [t for t in LOGIN_ATTEMPTS.get(ip, []) if now - t < 300]
    if len(recent) >= 5:
        raise HTTPException(429, 'Слишком много попыток. Повторите через пять минут.')
    if not auth.check_password(form.password):
        recent.append(now)
        LOGIN_ATTEMPTS[ip] = recent
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
            'modules': [{**m, 'enabled': cfg.get('enabled_modules', {}).get(m['id'], True),
                         'rollback_available': (DATA / 'modules' / m['id'] / 'previous').is_dir()}
                        for m in mods],
            'storage': {'total': disk.total, 'used': disk.used, 'free': disk.free},
            'trusted_keys': len(list((DATA / 'trusted_keys').glob('*.pem'))),
            'update_pending': (DATA / 'pending_update.json').exists(),
            'last_update': read_json(DATA / 'update_status.json'),
            'online_sources': online.source_status(),
            'cache': {'enabled': cfg['online_cache_enabled'],
                      'limit_gb': cfg['online_cache_gb']}}


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
    original = Path(file.filename or 'video.mp4').name
    suffix = Path(original).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(422, 'Поддерживаются MP4, WebM и M4V')
    free = shutil.disk_usage(DATA).free
    if free < 3 * 1024**3:
        raise HTTPException(507, 'Недостаточно свободного места (резерв 3 ГБ)')
    maximum = min(10 * 1024**3, max(0, free - 3 * 1024**3))
    try:
        incoming = await save_upload(file, maximum, suffix)
        with incoming.open('rb') as handle:
            header = handle.read(12)
        if suffix in ('.mp4', '.m4v') and header[4:8] != b'ftyp':
            raise ValueError('Это не MP4/M4V-файл')
        if suffix == '.webm' and not header.startswith(b'\x1a\x45\xdf\xa3'):
            raise ValueError('Это не WebM-файл')
        safe_name = re.sub(r'[^0-9A-Za-zА-Яа-яЁё._-]+', '_', original)[:100].strip('._-')
        if not safe_name or not safe_name.lower().endswith(suffix):
            safe_name = 'video' + suffix
        target = DATA / 'videos' / (secrets.token_hex(4) + '-' + safe_name)
        os.replace(incoming, target)
        return {'ok': True, 'name': target.name}
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
    if target.is_symlink() or not target.is_file():
        raise HTTPException(404)
    target.unlink()
    return {'ok': True}


@app.post('/api/admin/modules/install')
async def install_module(file: UploadFile = File(...), _: None = Depends(auth.require_admin)):
    try:
        incoming = await save_upload(file, bundles.MAX_BUNDLE_BYTES, '.zip')
        manifest = await asyncio.to_thread(bundles.install_module, incoming)
        return {'ok': True, 'module': manifest}
    except (ValueError, OSError, __import__('zipfile').BadZipFile) as exc:
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
    if (DATA / 'pending_update.json').exists():
        raise HTTPException(409, 'Предыдущее обновление ещё выполняется')
    try:
        incoming = await save_upload(file, bundles.MAX_BUNDLE_BYTES, '.zip')
        manifest = await asyncio.to_thread(bundles.stage_core, incoming)
        return {'ok': True, 'version': manifest['version'], 'message': 'Обновление поставлено в очередь с откатом'}
    except (ValueError, OSError, __import__('zipfile').BadZipFile) as exc:
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
        atomic_json(DATA / 'pending_update.json', {'source': 'github', 'revision': latest})
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
