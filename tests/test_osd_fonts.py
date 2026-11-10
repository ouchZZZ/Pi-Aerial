"""Enumerate installed font families safely, without a shell or font downloads."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app import osd_fonts, main
from app.storage import update_settings
from fastapi.testclient import TestClient
from app import auth


def test_fontconfig_lists_unique_sorted_primary_families(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=(
            'Noto Sans\\nDejaVu Serif\\nNoto Sans\\n'
            'Liberation Mono\\nBad\\x01Font\\n\\n'
        ))

    monkeypatch.setattr(osd_fonts.subprocess, 'run', fake_run)
    assert osd_fonts.installed_font_families() == [
        'DejaVu Serif', 'Liberation Mono', 'Noto Sans',
    ]
    assert calls[0][0] == ['fc-list', '-f', '%{family[0]}\\n']
    assert calls[0][1]['timeout'] == 5
    assert 'shell' not in calls[0][1]


@pytest.mark.parametrize('failure', [
    OSError('fontconfig missing'),
    subprocess.TimeoutExpired(['fc-list'], timeout=5),
])
def test_font_listing_gracefully_handles_unavailable_fontconfig(monkeypatch, failure):
    def bad_run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(osd_fonts.subprocess, 'run', bad_run)
    assert osd_fonts.installed_font_families() == []


def test_fontconfig_errors_are_nonfatal(monkeypatch):
    monkeypatch.setattr(osd_fonts.subprocess, 'run',
                        lambda *a, **kw: SimpleNamespace(returncode=1, stdout='oops'))
    assert osd_fonts.installed_font_families() == []


def test_dynamic_font_names_allow_unicode_and_reject_control_characters():
    assert osd_fonts.valid_font_choice('installed:Noto Sans')
    assert osd_fonts.valid_font_choice('installed:Русский шрифт')
    assert osd_fonts.valid_font_choice('georgia')
    for invalid in (
        'installed:', 'installed:  font', 'installed:font\\nname',
        'installed:font\\x00name', 'installed:' + 'x' * 161,
        'not-in-list', False, [], None,
    ):
        assert not osd_fonts.valid_font_choice(invalid)


def test_font_list_api_is_admin_only(monkeypatch):
    monkeypatch.setattr(osd_fonts, 'installed_font_families',
                        lambda: ['DejaVu Sans', 'Noto Sans'])
    auth.set_password('not-a-short-password')
    with TestClient(main.app) as client:
        assert client.get('/api/admin/osd/fonts').status_code == 401
        login = client.post('/api/auth/login', json={'password': 'not-a-short-password'})
        assert login.status_code == 200
        response = client.get('/api/admin/osd/fonts')
        assert response.status_code == 200
        assert response.json() == {'fonts': ['DejaVu Sans', 'Noto Sans']}
