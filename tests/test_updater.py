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
    monkeypatch.setattr(helper, 'healthy', lambda version, revision=None: False)
    with pytest.raises(RuntimeError, match='не прошла'):
        helper.main()
    assert (tmp_path / 'current').resolve() == releases / '0.1.0'
    assert len(calls) == 2  # try release, then revert release
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
