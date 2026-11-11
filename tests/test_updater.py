"""The updater must restore the working release when health verification fails."""
import importlib.util
import json
from pathlib import Path

import pytest


def test_unhealthy_core_update_rolls_back(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('pi_aerial_update_helper',
        Path(__file__).parents[1] / 'tools' / 'apply_core_update.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    releases = tmp_path / 'releases'
    (releases / '0.1.0').mkdir(parents=True)
    (releases / '0.2.0').mkdir()
    (releases / '0.2.0' / 'VERSION').write_text('0.2.0\n')
    (releases / '0.1.0' / 'VERSION').write_text('0.1.0\n')
    (releases / '0.1.0' / 'requirements.txt').write_text('')
    (releases / '0.2.0' / 'requirements.txt').write_text('')
    (tmp_path / 'current').symlink_to(releases / '0.1.0', target_is_directory=True)
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'pending_update.json').write_text(json.dumps({'version': '0.2.0'}))
    monkeypatch.setattr(helper, 'BASE', tmp_path)
    monkeypatch.setattr(helper, 'DATA', data)
    monkeypatch.setattr(helper, 'PENDING', data / 'pending_update.json')
    monkeypatch.setattr(helper, 'STATUS', data / 'update_status.json')
    monkeypatch.setattr(helper, 'background_ready', lambda: True)
    calls = []
    monkeypatch.setattr(helper, 'service_restart', lambda: calls.append('restart'))
    monkeypatch.setattr(helper, 'prepare_venv', lambda target, old: None)
    monkeypatch.setattr(helper, 'healthy', lambda version, revision=None: version == '0.1.0')
    with pytest.raises(RuntimeError, match='не прошла'):
        helper.main()
    assert (tmp_path / 'current').resolve() == releases / '0.1.0'
    assert len(calls) == 2  # try release, then revert release
    status = json.loads((data / 'update_status.json').read_text())
    assert status['ok'] is False
    assert 'восстановлена предыдущая версия' in status['message']
    assert not (data / 'pending_update.json').exists()


@pytest.mark.parametrize("automatic", [False, True])
def test_busy_media_defers_without_staging_restart_or_failure(monkeypatch, tmp_path, automatic):
    spec = importlib.util.spec_from_file_location('deferred_helper',
        Path(__file__).parents[1] / 'tools' / 'apply_core_update.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    old = tmp_path / 'releases' / 'old'
    old.mkdir(parents=True)
    (tmp_path / 'current').mkdir()
    data = tmp_path / 'data'
    data.mkdir()
    payload = {'source': 'github', 'revision': 'a' * 40}
    pending = {**payload, '_automatic': True} if automatic else payload
    (data / 'pending_update.json').write_text(json.dumps(pending))
    for name, value in [('BASE', tmp_path), ('DATA', data),
                        ('PENDING', data / 'pending_update.json'),
                        ('STATUS', data / 'update_status.json')]:
        monkeypatch.setattr(helper, name, value)
    monkeypatch.setattr(helper, 'background_ready', lambda: False)
    monkeypatch.setattr(helper, 'stage_github', lambda *args: pytest.fail('staged busy update'))
    monkeypatch.setattr(helper, 'service_restart', lambda: pytest.fail('interrupted media'))
    helper.main()
    assert not helper.PENDING.exists()
    assert not helper.STATUS.exists()
    deferred = json.loads((data / 'update_request.json').read_text())
    assert deferred['payload'] == payload
    assert deferred['automatic'] is automatic



def test_restart_only_queues_systemd_job(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'nonblocking_restart_helper',
        Path(__file__).parents[1] / 'tools' / 'apply_core_update.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    calls = []
    monkeypatch.setattr(helper.subprocess, 'run',
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    helper.service_restart()
    assert calls == [(
        (['systemctl', '--user', 'restart', '--no-block', 'pi-aerial.service'],),
        {'check': True, 'timeout': 10},
    )]


def test_health_never_accepts_old_git_revision(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'revision_health_helper',
        Path(__file__).parents[1] / 'tools' / 'apply_core_update.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class Response:
        def __init__(self, revision):
            self.revision = revision

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                'ok': True, 'version': '0.5.0',
                'revision': self.revision,
            }).encode()

    readings = iter(['old', 'new'])
    monkeypatch.setattr(helper.urllib.request, 'urlopen',
                        lambda *args, **kwargs: Response(next(readings)))
    monkeypatch.setattr(helper.time, 'sleep', lambda _: None)
    assert helper.healthy('0.5.0', 'new')


def test_failed_rollback_health_reports_error(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        'rollback_health_helper',
        Path(__file__).parents[1] / 'tools' / 'apply_core_update.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    releases = tmp_path / 'releases'
    old = releases / 'old'
    new = releases / '0.2.0'
    old.mkdir(parents=True)
    new.mkdir()
    (old / 'VERSION').write_text('0.1.0')
    (new / 'VERSION').write_text('0.2.0')
    (tmp_path / 'current').symlink_to(old, target_is_directory=True)
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'pending_update.json').write_text(json.dumps({'version': '0.2.0'}))
    for name, value in [('BASE', tmp_path), ('DATA', data),
                        ('PENDING', data / 'pending_update.json'),
                        ('STATUS', data / 'update_status.json')]:
        monkeypatch.setattr(helper, name, value)
    monkeypatch.setattr(helper, 'background_ready', lambda: True)
    monkeypatch.setattr(helper, 'prepare_venv', lambda target, old: None)
    monkeypatch.setattr(helper, 'ensure_system_dependencies', lambda: None)
    monkeypatch.setattr(helper, 'service_restart', lambda: None)
    monkeypatch.setattr(helper, 'healthy', lambda version, revision=None: False)
    with pytest.raises(RuntimeError, match='не прошла'):
        helper.main()
    assert (tmp_path / 'current').resolve() == old
    status = json.loads((data / 'update_status.json').read_text())
    assert status['ok'] is False
    assert 'Ошибка отката' in status['message']
