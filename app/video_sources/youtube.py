"""YouTube links resolve to an official embedded player, never a download."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, SplitResult

from .base import ResolvedVideo

PROVIDER = 'youtube'
_HOSTS = frozenset({
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'music.youtube.com', 'www.youtube-nocookie.com',
    'youtube-nocookie.com', 'youtu.be', 'www.youtu.be',
})
_VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}\Z')


def parse(parts: SplitResult) -> ResolvedVideo | None:
    hostname = (parts.hostname or '').lower().rstrip('.')
    if hostname not in _HOSTS:
        return None

    segments = [segment for segment in parts.path.split('/') if segment]
    if hostname in ('youtu.be', 'www.youtu.be'):
        video_id = segments[0] if len(segments) == 1 else ''
    elif parts.path == '/watch':
        values = parse_qs(parts.query).get('v', [])
        video_id = values[0] if len(values) == 1 else ''
    elif len(segments) == 2 and segments[0] in ('shorts', 'live', 'embed', 'v'):
        video_id = segments[1]
    else:
        video_id = ''

    if not _VIDEO_ID.fullmatch(video_id):
        from .registry import InvalidVideoURL
        raise InvalidVideoURL('Нужна ссылка на конкретный ролик YouTube')
    return ResolvedVideo(provider=PROVIDER, video_id=video_id)


def describe(video_id: str) -> dict:
    if not _VIDEO_ID.fullmatch(video_id):
        raise ValueError('Invalid YouTube video ID')
    return {'kind': 'embed', 'provider': PROVIDER, 'video_id': video_id}
