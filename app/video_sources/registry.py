"""Resolve untrusted URLs into provider-owned, validated video identifiers.

New providers are added to SOURCES with parse(parts) and describe(video_id).
A provider never sends arbitrary user-supplied URLs to the kiosk browser.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from . import youtube, vkvideo
from .base import ResolvedVideo


class InvalidVideoURL(ValueError):
    """Unsupported or malformed external video link."""


SOURCES = (youtube, vkvideo)


def resolve(value: str) -> ResolvedVideo:
    if not isinstance(value, str) or len(value) > 2048:
        raise InvalidVideoURL('Недопустимая длина ссылки')
    url = value.strip()
    if not url or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise InvalidVideoURL('Недопустимая ссылка')
    try:
        parts = urlsplit(url)
        # No credentials, custom ports, localhost or internal-network requests.
        if parts.scheme != 'https' or not parts.hostname or parts.username is not None or parts.password is not None or parts.port is not None:
            raise InvalidVideoURL('Нужна обычная HTTPS-ссылка на видео')
    except ValueError as exc:
        raise InvalidVideoURL('Некорректный адрес видео') from exc
    for provider in SOURCES:
        video = provider.parse(parts)
        if video is not None:
            return video
    raise InvalidVideoURL('Этот видеохостинг пока не поддерживается')


def providers() -> frozenset[str]:
    return frozenset(source.PROVIDER for source in SOURCES)


def describe(provider: str, video_id: str) -> dict:
    for source in SOURCES:
        if source.PROVIDER == provider:
            return source.describe(video_id)
    raise InvalidVideoURL('Источник видео не найден')
