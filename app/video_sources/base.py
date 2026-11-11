"""Small shared contract used by manually played URL providers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedVideo:
    provider: str
    video_id: str
