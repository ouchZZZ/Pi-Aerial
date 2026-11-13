"""Integration tests for local management API and signed extensions."""
import base64
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

# Isolate all test storage from any real Pi Aerial installation.
TEST_BASE = Path(tempfile.mkdtemp(prefix='pi-aerial-test-'))
os.environ['PI_AERIAL_BASE'] = str(TEST_BASE)
os.environ['PI_AERIAL_DATA_DIR'] = str(TEST_BASE / 'data')

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from app import auth, bundles, main, weather
from app.storage import DATA, init_data

init_data()
auth.set_password('not-a-short-password')


def setup_key():
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    (DATA / 'trusted_keys' / 'test.pem').write_bytes(pub)
    return key


def make_bundle(key, kind, ident='demo-module', version='1.0.0'):
    buf = io.BytesIO()
    manifest = {'kind': kind, 'id': ident if kind == 'module' else 'pi-aerial',
                'name': 'Demo', 'version': version, 'entry': 'index.html', 'position': 'top-right'}
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        if kind == 'module':
            z.writestr('index.html', '<html><body>Widget</body></html>')
        else:
            z.writestr('VERSION', version+'\n')
            z.writestr('app/main.py', '# example')
            z.writestr('web/display.html', '<html></html>')
    buf.seek(0)
    with zipfile.ZipFile(buf) as z:
        payload = bundles.signed_payload(z)
    with zipfile.ZipFile(buf, 'a') as z:
        z.writestr('SIGNATURE', base64.b64encode(key.sign(payload)))
    return buf.getvalue()


def login_client(client):
    r = client.post('/api/auth/login', json={'password': 'not-a-short-password'})
    assert r.status_code == 200, r.text
    return {'x-csrf-token': r.json()['csrf']}


def test_screen_and_protected_api(monkeypatch):
    monkeypatch.setattr(weather, 'refresh_weather', lambda: {'available': False})
    with TestClient(main.app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/admin').status_code == 200
        assert client.get('/api/health').json()['version'] == (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
        assert client.get('/api/public').status_code == 200
        assert client.get('/api/admin/status').status_code == 401
        csrf = login_client(client)
        assert client.get('/api/admin/status').status_code == 200
        denied = client.put('/api/admin/config', json={'changes': {'city': 'Абакан'}})
        assert denied.status_code == 403
        denied_origin = client.put('/api/admin/config', json={'changes': {'city': 'Абакан'}},
                                   headers={**csrf, 'Origin': 'http://attacker.test'})
        assert denied_origin.status_code == 403
        ok = client.put('/api/admin/config', json={'changes': {'city': 'Абакан'}}, headers=csrf)
        assert ok.status_code == 200, ok.text
        assert client.get('/api/public').json()['settings']['city'] == 'Абакан'
        bad_zone = client.put('/api/admin/config', json={'changes': {'timezone': 'Wrong/Zone'}}, headers=csrf)
        assert bad_zone.status_code == 422
        response = client.post('/api/admin/display/reload', headers=csrf)
        assert response.status_code == 200
        assert client.get('/api/public').json()['reload_counter'] >= 1



def test_kiosk_referrer_policy_allows_youtube_without_exposing_api_urls(monkeypatch):
    """YouTube rejects embedded playback (error 153) without Referer.

    Public document and admin map can send the origin only; API/assets cannot
    reveal route or token details to cross-origin requests.
    """
    monkeypatch.setattr(weather, 'refresh_weather', lambda: {'available': False})
    with TestClient(main.app) as client:
        for route in ('/', '/admin'):
            page = client.get(route)
            assert page.status_code == 200
            assert page.headers['referrer-policy'] == 'strict-origin-when-cross-origin'
            if route == '/':
                assert page.headers['permissions-policy'] == 'camera=(), microphone=()'
            else:
                assert 'permissions-policy' not in page.headers
        assert client.get('/').headers['cache-control'] == 'no-store'
        for route in ('/api/health', '/api/display/play-once', '/assets/display.js'):
            response = client.get(route)
            assert response.status_code == 200
            assert response.headers['referrer-policy'] == 'no-referrer'



def test_signed_modules_and_rollback(monkeypatch):
    monkeypatch.setattr(weather, 'refresh_weather', lambda: {})
    key = setup_key()
    with TestClient(main.app) as client:
        headers = login_client(client)
        first = make_bundle(key, 'module', version='1.0.0')
        response = client.post('/api/admin/modules/install', files={'file': ('module.zip', first, 'application/zip')}, headers=headers)
        assert response.status_code == 200, response.text
        assert client.get('/modules/demo-module').status_code == 200
        assert 'Demo' in str(client.get('/api/admin/status').json()['modules'])
        second = make_bundle(key, 'module', version='1.1.0')
        response = client.post('/api/admin/modules/install', files={'file': ('module.zip', second, 'application/zip')}, headers=headers)
        assert response.status_code == 200, response.text
        assert client.get('/api/admin/status').json()['modules'][0]['version'] == '1.1.0'
        response = client.post('/api/admin/modules/demo-module/rollback', headers=headers)
        assert response.status_code == 200, response.text
        assert client.get('/api/admin/status').json()['modules'][0]['version'] == '1.0.0'
        toggled = client.put('/api/admin/modules/demo-module/enabled', json={'enabled': False}, headers=headers)
        assert toggled.status_code == 200
        assert client.get('/api/public').json()['modules'] == []
        # Tampering with one byte invalidates the signature.
        with zipfile.ZipFile(io.BytesIO(first)) as z:
            manifest = json.loads(z.read('manifest.json'))
        manifest['version'] = '9.9.9'
        tampered = io.BytesIO()
        with zipfile.ZipFile(tampered, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            z.writestr('index.html', '<html></html>')
            with zipfile.ZipFile(io.BytesIO(first)) as orig:
                z.writestr('SIGNATURE', orig.read('SIGNATURE'))
        bad = client.post('/api/admin/modules/install', files={'file': ('bad.zip', tampered.getvalue(), 'application/zip')}, headers=headers)
        assert bad.status_code == 422


def test_safe_upload_and_core_queue(monkeypatch):
    monkeypatch.setattr(weather, 'refresh_weather', lambda: {})
    # File validation must not depend on the host's available space.
    monkeypatch.setattr(main.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=100 * 1024**3, total=200 * 1024**3, used=100 * 1024**3))
    key = setup_key()
    monkeypatch.setattr(main.local_videos, 'polling_loop', lambda stop: stop.wait(30))
    with TestClient(main.app) as client:
        headers = login_client(client)
        invalid = client.post('/api/admin/videos', files={'file': ('wrong.mp4', b'NOT_A_VIDEO', 'video/mp4')}, headers=headers)
        assert invalid.status_code == 422
        fake_mp4 = b'\x00\x00\x00\x18ftypisom' + b'0' * 100
        uploaded = client.post('/api/admin/videos', files={'file': ('test.mp4', fake_mp4, 'video/mp4')}, headers=headers)
        assert uploaded.status_code == 200, uploaded.text
        job = uploaded.json()
        assert job['state'] == 'queued'
        assert any(item['id'] == job['id']
                   for item in client.get('/api/admin/status').json()['video_processing'])
        assert not any(item['name'] == job['name']
                       for item in client.get('/api/public').json()['videos'])
        assert client.delete('/api/admin/videos/jobs/'+job['id'], headers=headers).status_code == 200
        core = make_bundle(key, 'core', version='0.2.0')
        queued = client.post('/api/admin/core/install', files={'file': ('core.zip', core, 'application/zip')}, headers=headers)
        assert queued.status_code == 200, queued.text
        assert (DATA / 'pending_update.json').exists()
        assert (TEST_BASE / 'releases' / '0.2.0' / 'app' / 'main.py').exists()
        (DATA / 'update_request.json').unlink(missing_ok=True)
        (DATA / 'pending_update.json').unlink()


def test_invalid_video_header_wins_over_low_disk_space(monkeypatch):
    monkeypatch.setattr(main.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=7 * 1024**3, total=8 * 1024**3, used=1 * 1024**3))
    with TestClient(main.app) as client:
        headers = login_client(client)
        invalid = client.post('/api/admin/videos',
                              files={'file': ('bad.mp4', b'NOT_A_VIDEO', 'video/mp4')}, headers=headers)
        assert invalid.status_code == 422
        valid_header = b'\x00\x00\x00\x18ftypisom' + b'0' * 100
        no_space = client.post('/api/admin/videos',
                               files={'file': ('valid.mp4', valid_header, 'video/mp4')}, headers=headers)
        assert no_space.status_code == 507


def test_failed_login_limits_ip_history_and_prunes_expired(monkeypatch):
    now = main.time.monotonic()
    monkeypatch.setattr(main, 'LOGIN_ATTEMPTS_MAX_IPS', 2)
    with main.LOGIN_ATTEMPTS_LOCK:
        main.LOGIN_ATTEMPTS.clear()
        main.LOGIN_ATTEMPTS.update({
            'expired': [now - 301],
            'oldest': [now - 200],
            'newer': [now - 100],
        })
    request = Request({'type': 'http', 'client': ('new-ip', 42),
                       'scheme': 'http', 'server': ('localhost', 80),
                       'path': '/api/auth/login', 'headers': []})
    try:
        with pytest.raises(HTTPException) as exc:
            main.login(main.Login(password='incorrect-password'), request)
        assert exc.value.status_code == 401
        assert set(main.LOGIN_ATTEMPTS) == {'newer', 'new-ip'}
    finally:
        with main.LOGIN_ATTEMPTS_LOCK:
            main.LOGIN_ATTEMPTS.clear()


def test_local_source_toggle_preserves_uploaded_files(monkeypatch):
    """Manual uploads disappear from the playlist, but stay in admin and on disk."""
    from app import online
    from app.storage import update_settings
    monkeypatch.setattr(weather, 'refresh_weather', lambda: {})
    monkeypatch.setattr(online, 'list_clips', lambda: [])
    name = 'hand_uploaded_test.mp4'
    video = DATA / 'videos' / name
    video.write_bytes(b'\\x00\\x00\\x00\\x18ftypisom' + b'0' * 100)
    main.local_videos.mark_ready(video)
    try:
        update_settings({'local_videos_enabled': True})
        with TestClient(main.app) as client:
            csrf = login_client(client)
            assert any(item['name'] == name
                       for item in client.get('/api/public').json()['videos'])
            response = client.put('/api/admin/config',
                                  json={'changes': {'local_videos_enabled': False}},
                                  headers=csrf)
            assert response.status_code == 200, response.text
            assert not any(item['name'] == name
                           for item in client.get('/api/public').json()['videos'])
            assert any(item['name'] == name
                       for item in client.get('/api/admin/status').json()['videos'])
            assert video.exists()
            # Disabled rotation must not block the administrator's one-time show.
            assert client.get('/media/' + name).status_code == 200
            request = client.post('/api/admin/display/play-once/local/' + name,
                                  headers=csrf)
            assert request.status_code == 200, request.text
            command = client.get('/api/display/play-once').json()
            assert command['url'].startswith('/api/display/play-once/')
            shown = client.get(command['url'])
            assert shown.status_code == 200
            assert shown.content == video.read_bytes()
            assert not any(item['name'] == name
                           for item in client.get('/api/public').json()['videos'])
            assert client.post('/api/admin/display/play-once/stop',
                               headers=csrf).status_code == 200
            response = client.put('/api/admin/config',
                                  json={'changes': {'local_videos_enabled': True}},
                                  headers=csrf)
            assert response.status_code == 200, response.text
            assert any(item['name'] == name
                       for item in client.get('/api/public').json()['videos'])
            assert client.put('/api/admin/config',
                              json={'changes': {'local_videos_enabled': 'off'}},
                              headers=csrf).status_code == 422
    finally:
        update_settings({'local_videos_enabled': True})
        video.unlink(missing_ok=True)
        main.local_videos.remove_ready(video)


def test_sequential_lifespans_do_not_revive_workers_or_leak_jobs(monkeypatch, tmp_path):
    """Each TestClient must own a distinct stopped event."""
    from app import github_updates, update_control

    seen = []

    def idle(stop):
        seen.append(stop)
        stop.wait()

    for obj, name in (
        (main, "_online_loop"),
        (main.weather, "polling_loop"),
        (main.local_videos, "polling_loop"),
        (main.github_updates, "polling_loop"),
    ):
        monkeypatch.setattr(obj, name, idle)

    monkeypatch.setattr(update_control, "DATA", tmp_path)
    monkeypatch.setattr(github_updates, "DATA", tmp_path)
    for _ in range(2):
        before = len(seen)
        with TestClient(main.app) as client:
            assert client.get("/api/health").status_code == 200
        cohort = seen[before:]
        assert len(cohort) == 4
        assert all(event.is_set() for event in cohort)

    assert len(set(seen)) == 2  # The old global STOP would reuse one event.
    update_control.request({"version": "1.2.3"})
    assert update_control.ready()
