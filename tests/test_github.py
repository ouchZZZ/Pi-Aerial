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
        (DATA / 'update_request.json').unlink(missing_ok=True)
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


def test_public_https_configuration_without_deploy_key(monkeypatch, tmp_path):
    monkeypatch.setattr(github_updates, 'DATA', tmp_path)
    (tmp_path / 'github.json').write_text(json.dumps({
        'enabled': True, 'remote': github_updates.PUBLIC_REMOTE
    }))
    assert github_updates._config() == (github_updates.PUBLIC_REMOTE, None)
    monkeypatch.setenv('GIT_SSH_COMMAND', 'ssh -i /tmp/injected')
    env = github_updates._git_env(None)
    assert 'GIT_SSH_COMMAND' not in env
    assert env['GIT_TERMINAL_PROMPT'] == '0'


def test_legacy_ssh_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(github_updates, 'DATA', tmp_path)
    key = tmp_path / 'my deploy key'
    key.write_text('example')
    (tmp_path / 'github.json').write_text(json.dumps({
        'enabled': True, 'remote': github_updates.LEGACY_SSH_REMOTE,
        'ssh_key': str(key)
    }))
    assert github_updates._config() == (github_updates.LEGACY_SSH_REMOTE, key)
    import shlex
    assert shlex.quote(str(key)) in github_updates._git_env(key)['GIT_SSH_COMMAND']


def test_reject_untrusted_remotes(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(github_updates, 'DATA', tmp_path)
    (tmp_path / 'github.json').write_text(json.dumps({
        'enabled': True, 'remote': 'https://not-our-repo.example/test.git'
    }))
    with pytest.raises(ValueError, match='Некорректный'):
        github_updates._config()


def test_manual_update_drains_media_and_rejects_new_uploads(monkeypatch):
    from app import update_control
    sha = 'c' * 40
    monkeypatch.setattr(github_updates, 'remote_revision', lambda: sha)
    monkeypatch.setattr(github_updates, 'installed_revision', lambda: 'b' * 40)
    with TestClient(main.app) as client:
        headers = auth_headers(client)
        try:
            with update_control.media_job() as admitted:
                assert admitted
                response = client.post('/api/admin/github/update', headers=headers)
                assert response.status_code == 200
                assert not (DATA / 'pending_update.json').exists()
                assert client.get('/api/health').json()['update_ready'] is False
                status = client.get('/api/admin/status').json()
                assert status['update_pending'] is True
                assert status['update_waiting'] is True
                upload = client.post('/api/admin/videos',
                    files={'file': ('clip.mp4', b'0000ftyp0000', 'video/mp4')},
                    headers=headers)
                assert upload.status_code == 503
            update_control.promote()
            assert json.loads((DATA / 'pending_update.json').read_text())['revision'] == sha
            assert client.get('/api/health').json()['update_ready'] is True
        finally:
            (DATA / 'update_request.json').unlink(missing_ok=True)
            (DATA / 'pending_update.json').unlink(missing_ok=True)
