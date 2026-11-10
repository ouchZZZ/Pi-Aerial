"""Paths, persistent state and safe, atomic JSON writes."""
from __future__ import annotations

import json
import os
import re
import shutil
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

ONLINE_CACHE_RESERVE_GB = 5
GIB = 1024 ** 3

DEFAULTS = {
    'city': 'Саяногорск', 'latitude': 53.1000, 'longitude': 91.4000,
    'timezone': 'Asia/Krasnoyarsk', 'show_clock': True,
    'show_date': True, 'show_weather': True,
    'time_format': '24', 'clock_position': 'bottom-left',
    'video_mode': 'shuffle', 'video_interval_minutes': 0,
    'night_mode': True, 'night_start': 23, 'night_end': 7,
    'night_opacity': 0.45, 'clock_shift': True,
    'enabled_modules': {},
    'local_videos_enabled': True,
    'online_sources': {'apple': True, 'nasa': True},
    'online_cache_enabled': True,
    'online_autodownload_enabled': True,
    'online_cache_gb': 12,
}


def init_data() -> None:
    for folder in [DATA, DATA / 'videos', DATA / 'modules', DATA / 'module_backups',
                   DATA / 'trusted_keys', DATA / 'incoming', DATA / 'weather_cache',
                   DATA / 'online_cache']:
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
        result = {**DEFAULTS, **read_json(DATA / 'settings.json')}
        # Upgrade old settings transparently without dropping Apple/NASA choices.
        sources = result.get('online_sources')
        if isinstance(sources, dict):
            result['online_sources'] = {
                name: sources.get(name, enabled)
                for name, enabled in DEFAULTS['online_sources'].items()
            }
        return result


def max_online_cache_gb() -> int:
    """The cache maximum follows the storage device, not an arbitrary 64 GiB."""
    try:
        total_gb = shutil.disk_usage(DATA).total // GIB
    except OSError:
        return DEFAULTS['online_cache_gb']
    # Preserve the old default for small development/test disks.
    return max(DEFAULTS['online_cache_gb'], total_gb - ONLINE_CACHE_RESERVE_GB)


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
    sources = result.get('online_sources')
    if (not isinstance(sources, dict)
            or set(sources) != {'apple', 'nasa'}
            or any(not isinstance(value, bool) for value in sources.values())):
        raise ValueError('Неверные переключатели онлайн-источников')
    if not isinstance(result['online_cache_enabled'], bool):
        raise ValueError('Неверный режим кеширования')
    cache_limit = result['online_cache_gb']
    maximum = max_online_cache_gb()
    if type(cache_limit) is not int or not 1 <= cache_limit <= maximum:
        raise ValueError(f'Лимит кеша должен быть от 1 до {maximum} ГБ')
    for key in ('show_clock', 'show_date', 'show_weather', 'night_mode', 'clock_shift',
                'local_videos_enabled', 'online_autodownload_enabled'):
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
