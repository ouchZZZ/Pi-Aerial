"""Language, Moscow defaults, and time-zone validation."""
import json
from app import storage
from app.storage import DEFAULTS


def test_fresh_install_defaults_to_moscow_and_russian(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    initial = storage.settings()
    assert initial['city'] == 'Москва'
    assert initial['latitude'] == 55.7558
    assert initial['longitude'] == 37.6173
    assert initial['timezone'] == 'Europe/Moscow'
    assert initial['language'] == 'ru'
    assert DEFAULTS['language'] == 'ru'


def test_existing_install_keeps_location_when_language_is_added(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    (tmp_path / 'settings.json').write_text(json.dumps({
        'city': 'Саяногорск', 'latitude': 53.1, 'longitude': 91.4,
        'timezone': 'Asia/Krasnoyarsk'
    }), encoding='utf8')
    before = storage.settings()
    assert before['city'] == 'Саяногорск'
    assert before['timezone'] == 'Asia/Krasnoyarsk'
    assert before['language'] == 'ru'
    result = storage.update_settings({'language': 'en'})
    assert result['language'] == 'en'
    assert result['city'] == 'Саяногорск'
    assert result['timezone'] == 'Asia/Krasnoyarsk'


def test_only_supported_languages_and_real_coordinates_are_accepted(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    good = storage.update_settings({
        'city': 'London', 'latitude': 51.5072, 'longitude': -0.1276,
        'timezone': 'Europe/London', 'language': 'en'
    })
    assert good['timezone'] == 'Europe/London'
    for bad in ['fr', 'de', '', None, [], 3]:
        with pytest.raises(ValueError, match='Unsupported interface language'):
            storage.update_settings({'language': bad})
    for bad in ['NaN', 'Infinity', None, 'bad coordinates']:
        with pytest.raises(ValueError, match='Неверные координаты'):
            storage.update_settings({'latitude': bad})


def test_timezone_list_requires_admin(monkeypatch):
    from fastapi.testclient import TestClient
    from app import auth, main, weather
    monkeypatch.setattr(weather, 'refresh_weather', lambda: {'available': False})
    auth.set_password('not-a-short-password')
    with TestClient(main.app) as client:
        assert client.get('/api/admin/timezones').status_code == 401
        login = client.post('/api/auth/login', json={'password': 'not-a-short-password'})
        assert login.status_code == 200
        zones = client.get('/api/admin/timezones').json()['timezones']
        assert 'Europe/Moscow' in zones
        assert 'Asia/Krasnoyarsk' in zones
        assert zones == sorted(zones)
