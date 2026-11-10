"""Paths, persistent state and safe, atomic JSON writes."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE = Path(os.environ.get('PI_AERIAL_BASE', Path(__file__).resolve().parent.parent)).resolve()
DATA = Path(os.environ.get('PI_AERIAL_DATA_DIR', BASE / 'data')).resolve()
WEB = Path(__file__).resolve().parent.parent / 'web'
CURRENT_VERSION = (Path(__file__).resolve().parent.parent / 'VERSION').read_text(encoding='utf8').strip()
REVISION_FILE = Path(__file__).resolve().parent.parent / 'GIT_REVISION'
CURRENT_REVISION = REVISION_FILE.read_text(encoding='utf8').strip() if REVISION_FILE.is_file() else None
LOCK = RLock()

DEFAULTS = {
    'city': 'Саяногорск', 'latitude': 53.1000, 'longitude': 91.4000,
    'timezone': 'Asia/Krasnoyarsk', 'show_clock': True,
    'show_date': True, 'show_weather': True,
    'time_format': '24', 'clock_position': 'bottom-left',
    'video_mode': 'shuffle', 'video_interval_minutes': 0,
    'night_mode': True, 'night_start': 23, 'night_end': 7,
    'night_opacity': 0.45, 'clock_shift': True,
    'enabled_modules': {},
}


def init_data() -> None:
    for folder in [DATA, DATA / 'videos', DATA / 'modules', DATA / 'module_backups',
                   DATA / 'trusted_keys', DATA / 'incoming', DATA / 'weather_cache']:
        folder.mkdir(parents=True, exist_ok=True)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(handle, 'w', encoding='utf8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, fallback: dict | None = None) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value, dict) else dict(fallback or {})
    except (FileNotFoundError, ValueError, OSError):
        return dict(fallback or {})


def settings() -> dict:
    with LOCK:
        return {**DEFAULTS, **read_json(DATA / 'settings.json')}


def update_settings(new: dict) -> dict:
    allowed = set(DEFAULTS) - {'enabled_modules'}
    if set(new) - allowed:
        raise ValueError(f"Неизвестные параметры: {', '.join(sorted(set(new) - allowed))}")
    result = {**settings(), **new}
    if not (isinstance(result['city'], str) and 1 <= len(result['city'].strip()) <= 70):
        raise ValueError('Неверное название города')
    if not (-90 <= float(result['latitude']) <= 90 and -180 <= float(result['longitude']) <= 180):
        raise ValueError('Неверные координаты')
    try:
        ZoneInfo(result['timezone'])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError('Неизвестный часовой пояс') from None
    for key in ('show_clock', 'show_date', 'show_weather', 'night_mode', 'clock_shift'):
        if not isinstance(result[key], bool):
            raise ValueError(f'Параметр {key} должен быть логическим')
    if result['clock_position'] not in ('bottom-left', 'bottom-center', 'center', 'top-right'):
        raise ValueError('Неверное расположение часов')
    if str(result['time_format']) not in ('12', '24'):
        raise ValueError('Неверный формат часов')
    if result['video_mode'] not in ('shuffle', 'ordered'):
        raise ValueError('Неверный режим видео')
    if int(result['video_interval_minutes']) not in (0, 5, 10, 15, 30):
        raise ValueError('Неверный интервал видео')
    for key in ('night_start', 'night_end'):
        if not (0 <= int(result[key]) <= 23):
            raise ValueError(f'Неверный час {key}')
    if not (0.1 <= float(result['night_opacity']) <= 1.0):
        raise ValueError('Неверная яркость ночного режима')
    with LOCK:
        atomic_json(DATA / 'settings.json', result)
    return result


def safe_id(value: str) -> bool:
    return bool(re.fullmatch(r'[a-z][a-z0-9-]{1,45}', value))
