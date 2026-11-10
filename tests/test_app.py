"""Integration tests for local management API and signed extensions."""
import base64
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

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
    key = setup_key()
    with TestClient(main.app) as client:
        headers = login_client(client)
        invalid = client.post('/api/admin/videos', files={'file': ('wrong.mp4', b'NOT_A_VIDEO', 'video/mp4')}, headers=headers)
        assert invalid.status_code == 422
        fake_mp4 = b'\x00\x00\x00\x18ftypisom' + b'0' * 100
        uploaded = client.post('/api/admin/videos', files={'file': ('test.mp4', fake_mp4, 'video/mp4')}, headers=headers)
        assert uploaded.status_code == 200, uploaded.text
        name = uploaded.json()['name']
        assert client.get('/media/'+name).status_code == 200
        assert client.delete('/api/admin/videos/'+name, headers=headers).status_code == 200
        core = make_bundle(key, 'core', version='0.2.0')
        queued = client.post('/api/admin/core/install', files={'file': ('core.zip', core, 'application/zip')}, headers=headers)
        assert queued.status_code == 200, queued.text
        assert (DATA / 'pending_update.json').exists()
        assert (TEST_BASE / 'releases' / '0.2.0' / 'app' / 'main.py').exists()
        (DATA / 'pending_update.json').unlink()
