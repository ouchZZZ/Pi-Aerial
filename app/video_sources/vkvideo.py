"""VK Video links for one-time playback using VK's official embed + JS API.

Store only validated owner/video IDs and the optional VK embed access hash.
Do not request arbitrary user URLs or download video on the Pi.
"""
from __future__ import annotations

import re
from urllib.parse import SplitResult, parse_qs

from .base import ResolvedVideo

PROVIDER = 'vkvideo'
_HOSTS = frozenset({
    'vkvideo.ru', 'www.vkvideo.ru',
    'vk.com', 'www.vk.com', 'm.vk.com',
    'vk.ru', 'www.vk.ru', 'm.vk.ru',
})
_PAGE = re.compile(r'/video(-?[0-9]{1,20})_([0-9]{1,20})/?\Z')
_OVERLAY = re.compile(r'video(-?[0-9]{1,20})_([0-9]{1,20})(?:/[^?#]*)?\Z')
_NUMBER = re.compile(r'-?[0-9]{1,20}\Z')
_VIDEO_NUMBER = re.compile(r'[0-9]{1,20}\Z')
_HASH = re.compile(r'[a-fA-F0-9]{1,128}\Z')
_STORED = re.compile(r'(-?[0-9]{1,20})_([0-9]{1,20})(?:~([a-fA-F0-9]{1,128}))?\Z')


def _single(params: dict, key: str) -> str:
    values = params.get(key, [])
    return values[0] if len(values) == 1 else ''


def parse(parts: SplitResult) -> ResolvedVideo | None:
    hostname = (parts.hostname or '').lower().rstrip('.')
    if hostname not in _HOSTS:
        return None

    from .registry import InvalidVideoURL

    params = parse_qs(parts.query, keep_blank_values=True)
    owner_id = video_id = ''
    if parts.path == '/video_ext.php':
        owner_id = _single(params, 'oid')
        video_id = _single(params, 'id')
    else:
        match = _PAGE.fullmatch(parts.path)
        if match:
            owner_id, video_id = match.groups()
        else:
            # VK's overlay links commonly look like /video?z=video-123_456/pl_...
            overlay = _single(params, 'z')
            match = _OVERLAY.fullmatch(overlay)
            if match:
                owner_id, video_id = match.groups()

    if not _NUMBER.fullmatch(owner_id) or not _VIDEO_NUMBER.fullmatch(video_id):
        raise InvalidVideoURL('Нужна ссылка на конкретный ролик VK Видео')

    access_hash = _single(params, 'hash') if 'hash' in params else ''
    if 'hash' in params and (len(params['hash']) != 1 or not _HASH.fullmatch(access_hash)):
        raise InvalidVideoURL('Некорректный ключ доступа VK Видео')

    # No raw URL reaches the browser; the player constructs its own fixed host.
    stored = owner_id + '_' + video_id + ('~' + access_hash if access_hash else '')
    return ResolvedVideo(provider=PROVIDER, video_id=stored)


def describe(stored_id: str) -> dict:
    match = _STORED.fullmatch(stored_id)
    if match is None:
        raise ValueError('Invalid VK Video identifier')
    owner_id, video_id, access_hash = match.groups()
    result = {
        'kind': 'embed', 'provider': PROVIDER,
        'owner_id': owner_id, 'video_id': video_id,
    }
    if access_hash:
        result['hash'] = access_hash
    return result
