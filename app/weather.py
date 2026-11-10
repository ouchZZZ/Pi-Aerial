"""Open-Meteo weather poller: stale data is labelled, not misrepresented as live."""
from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from .storage import DATA, atomic_json, read_json, settings

CACHE = DATA / 'weather_cache' / 'latest.json'
_lock = threading.Lock()


def current_weather() -> dict:
    result = read_json(CACHE)
    if not result:
        return {'available': False, 'stale': True}
    age = max(0, int(time.time()) - int(result.get('fetched_at', 0)))
    return {**result, 'available': True, 'stale': age > 60 * 60, 'age_minutes': age // 60}


def refresh_weather() -> dict:
    if not _lock.acquire(blocking=False):
        return current_weather()
    try:
        cfg = settings()
        params = urllib.parse.urlencode({
            'latitude': cfg['latitude'], 'longitude': cfg['longitude'],
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day',
            'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code',
            'timezone': cfg['timezone'], 'forecast_days': 8,
        })
        url = 'https://api.open-meteo.com/v1/forecast?' + params
        request = urllib.request.Request(url, headers={'User-Agent': 'Pi-Aerial/0.1 (local weather display)'})
        with urllib.request.urlopen(request, timeout=15) as response:
            import json
            raw = json.loads(response.read(100_000))
        cur, daily = raw['current'], raw['daily']
        result = {
            'fetched_at': int(datetime.now(timezone.utc).timestamp()),
            'city': cfg['city'],
            'temperature': cur['temperature_2m'],
            'apparent': cur['apparent_temperature'],
            'humidity': cur['relative_humidity_2m'],
            'code': cur['weather_code'], 'wind': cur['wind_speed_10m'],
            'is_day': bool(cur['is_day']),
            'daily_max': daily['temperature_2m_max'][0],
            'daily_min': daily['temperature_2m_min'][0],
            'rain_probability': daily['precipitation_probability_max'][0],
            'forecast': [
                {'date': date, 'min': low, 'max': high, 'code': code}
                for date, low, high, code in zip(
                    daily.get('time', []), daily['temperature_2m_min'],
                    daily['temperature_2m_max'], daily.get('weather_code', []),
                )
            ],
        }
        atomic_json(CACHE, result)
        return current_weather()
    finally:
        _lock.release()


def polling_loop(stop: threading.Event) -> None:
    # Keep startup responsive even when the internet is unavailable.
    while not stop.is_set():
        try:
            refresh_weather()
        except Exception as exc:
            print(f'[weather] refresh failed: {type(exc).__name__}: {exc}', flush=True)
        stop.wait(25 * 60)
