"""GitHub updates are pinned to the owner repository and admin-only."""
from contextlib import contextmanager
import json
from pathlib import Path

from app import bundles, github_updates, main
from app.storage import DATA
from fastapi.testclient import TestClient


def auth_headers(client):
    r = client.post('/api/auth/login', json={'password': 'not-a-short-password'})
    assert r.status_code == 200
    return {'x-csrf-token': r.json()['csrf']}


def test_github_status_and_queue_require_admin(monkeypatch):
    sha = 'a' * 40
    monkeypatch.setattr(github_updates, 'remote_revision', lambda: sha)
    monkeypatch.setattr(github_updates, 'installed_revision', lambda: 'b' * 40)
    with TestClient(main.app) as client:
        assert client.get('/api/admin/github/status').status_code == 401
        headers = auth_headers(client)
        r = client.get('/api/admin/github/status')
        assert r.status_code == 200
        assert r.json()['update_available'] is True
        assert client.post('/api/admin/github/update').status_code == 403
        r = client.post('/api/admin/github/update', headers=headers)
        assert r.status_code == 200
        pending = json.loads((DATA / 'pending_update.json').read_text())
        assert pending == {'source': 'github', 'revision': sha}
        assert client.post('/api/admin/github/update', headers=headers).status_code == 409
        (DATA / 'pending_update.json').unlink()


def test_github_catalog_installs_valid_module(monkeypatch, tmp_path):
    repo = tmp_path / 'repo'
    source = repo / 'catalog' / 'hello-widget'
    source.mkdir(parents=True)
    source.joinpath('manifest.json').write_text(json.dumps({
        'id': 'hello-widget', 'kind': 'module', 'version': '1.0.0',
        'name': 'Hello', 'entry': 'index.html', 'position': 'top-right'
    }))
    source.joinpath('index.html').write_text('<body>Trusted</body>')

    @contextmanager
    def fake_checkout(expected_revision=None):
        yield repo, 'a' * 40

    monkeypatch.setattr(github_updates, 'checkout', fake_checkout)
    listing = github_updates.catalog()
    assert listing['modules'][0]['id'] == 'hello-widget'
    result, sha = github_updates.install_from_catalog('hello-widget')
    assert result['version'] == '1.0.0'
    assert sha == 'a' * 40
    assert (DATA / 'modules' / 'hello-widget' / 'current' / 'index.html').is_file()
    assert github_updates.catalog()['modules'][0]['installed_version'] == '1.0.0'
    source.joinpath('index.html').unlink()
    source.joinpath('index.html').symlink_to('/etc/passwd')
    try:
        github_updates.install_from_catalog('hello-widget')
        assert False, 'unsafe source unexpectedly accepted'
    except ValueError:
        pass
