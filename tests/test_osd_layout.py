"""Validation and persistence of visual OSD coordinates."""
from __future__ import annotations

import math

import pytest

from app import storage


def test_custom_osd_layout_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    blocks = {
        'clock': {'x': 6, 'y': 10, 'scale': 1.3},
        'weather': {'x': 6, 'y': 35, 'scale': 0.8},
        'calendar': {'x': 75.5, 'y': 10.5, 'scale': 1},
        'footer': {'x': 5, 'y': 92, 'scale': 0.6},
    }
    updated = storage.update_settings({'osd_layout': blocks})
    assert updated['osd_layout'] == blocks
    assert storage.settings()['osd_layout'] == blocks
    assert storage.update_settings({'osd_layout': {}})['osd_layout'] == {}
    assert storage.settings()['osd_layout'] == {}


@pytest.mark.parametrize('bad', [
    None, [], {'clock': {'x': 10, 'y': 10, 'scale': 1}},
    {name: {'x': -1, 'y': 10, 'scale': 1}
     for name in ('clock', 'weather', 'calendar', 'footer')},
    {name: {'x': 10, 'y': 10, 'scale': math.inf}
     for name in ('clock', 'weather', 'calendar', 'footer')},
    {name: {'x': 10, 'y': 10, 'scale': True}
     for name in ('clock', 'weather', 'calendar', 'footer')},
    {name: {'x': 10, 'y': 10, 'scale': 3}
     for name in ('clock', 'weather', 'calendar', 'footer')},
    {name: {'x': 10, 'y': 10, 'scale': 1, 'extra': 'bad'}
     for name in ('clock', 'weather', 'calendar', 'footer')},
])
def test_rejects_bad_osd_layout(monkeypatch, tmp_path, bad):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    with pytest.raises(ValueError):
        storage.update_settings({'osd_layout': bad})
