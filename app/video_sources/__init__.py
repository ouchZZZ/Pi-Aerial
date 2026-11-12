"""Validated, pluggable providers for manual URL playback."""

from .registry import InvalidVideoURL, describe, providers, resolve

__all__ = ['InvalidVideoURL', 'describe', 'providers', 'resolve']
