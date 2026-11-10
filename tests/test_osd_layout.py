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
    assert updated['osd_layout_mode'] == 'alternate'  # Current default behavior.
    assert storage.settings()['osd_layout'] == blocks
    updated = storage.update_settings({
        'osd_layout_mode': 'single', 'osd_layout_alt': {
            name: {**geometry, 'x': 100-geometry['x']}
            for name, geometry in blocks.items()
        }
    })
    assert updated['osd_layout_mode'] == 'single'
    assert updated['osd_layout_alt']['clock']['x'] == 94
    assert updated['osd_layout']['clock']['x'] == 6
    assert storage.update_settings({'osd_layout_mode': 'alternate'})['osd_layout_alt']['clock']['x'] == 94
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


@pytest.mark.parametrize('bad_mode', [None, '', 'random', True, 2, {}, []])
def test_rejects_bad_osd_mode(monkeypatch, tmp_path, bad_mode):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    with pytest.raises(ValueError, match='Неверный режим'):
        storage.update_settings({'osd_layout_mode': bad_mode})


def test_alternate_layout_is_validated_independently(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    storage.update_settings({'osd_layout_mode': 'alternate', 'osd_layout': {}})
    bad = {'clock': {'x': 15, 'y': 15, 'scale': 1}}
    with pytest.raises(ValueError, match='Неверный набор блоков'):
        storage.update_settings({'osd_layout_alt': bad})
    assert storage.settings()['osd_layout_alt'] == {}


def test_font_and_color_persist_per_block_and_per_position(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    blocks = ('clock', 'weather', 'calendar', 'footer')
    first = {name: {'x': 10, 'y': 20, 'scale': 1} for name in blocks}
    second = {name: {'x': 25, 'y': 30, 'scale': 1.2} for name in blocks}
    first['clock'].update(font='georgia', color='#AaBbCC')
    first['weather']['font'] = 'courier'
    second['clock'].update(font='arial', color='#102030')
    second['footer']['color'] = '#998877'
    updated = storage.update_settings({
        'osd_layout': first, 'osd_layout_alt': second,
        'osd_layout_mode': 'alternate'
    })
    assert updated['osd_layout']['clock']['color'] == '#AaBbCC'
    assert updated['osd_layout_alt']['clock']['color'] == '#102030'
    assert storage.settings()['osd_layout']['weather']['font'] == 'courier'
    assert 'color' not in storage.settings()['osd_layout']['calendar']


@pytest.mark.parametrize(('key', 'bad_value'), [
    ('font', 'Comic Sans; background:url(https://example.com)'),
    ('font', 'custom'), ('font', None), ('font', True),
    ('font', []), ('color', 'red'), ('color', '#fff'),
    ('color', '#12345678'), ('color', '#00000g'),
    ('color', 'url(javascript:alert(1))'),
    ('color', None), ('color', False)
])
def test_invalid_font_or_color_is_rejected(monkeypatch, tmp_path, key, bad_value):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    layout = {name: {'x': 12, 'y': 15, 'scale': 1} for name in
              ('clock', 'weather', 'calendar', 'footer')}
    layout['clock'][key] = bad_value
    with pytest.raises(ValueError):
        storage.update_settings({'osd_layout': layout})
    assert storage.settings()['osd_layout'] == {}
