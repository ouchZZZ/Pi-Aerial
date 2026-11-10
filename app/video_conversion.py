"""Prepare video for Raspberry Pi 4's reliable HTML5 H.264/MP4 playback.

Use lossless remux for compatible 8-bit AVC, otherwise transcode in a
low-priority, single-worker background job. Never replace playable cache until
the entire output is verified.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_FPS = 30
MAX_LEVEL = 42
MAX_BYTES = 550 * 1024 * 1024


def probe_video(source: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("ffprobe не установлен; установите sudo apt install -y ffmpeg")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,profile,pix_fmt,width,height,avg_frame_rate,level",
         "-of", "json", str(source)],
        check=True, text=True, capture_output=True, timeout=30,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise ValueError("В файле нет видеодорожки")
    stream = streams[0]
    if int(stream.get("width") or 0) <= 0 or int(stream.get("height") or 0) <= 0:
        raise ValueError("Не удалось определить размер видео")
    return stream


def _fps(stream: dict) -> float:
    try:
        return float(Fraction(stream.get("avg_frame_rate") or "0/1"))
    except (ValueError, ZeroDivisionError):
        return 0.0


def can_stream_copy(stream: dict) -> bool:
    """No quality loss if the encoded frames already fit the Pi 4 profile."""
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    fps = _fps(stream)
    level = stream.get("level")
    try:
        level = int(level or 0)
    except (ValueError, TypeError):
        return False
    return (
        stream.get("codec_name") == "h264"
        and stream.get("pix_fmt") == "yuv420p"
        and str(stream.get("profile", "")).lower() in
        ("baseline", "constrained baseline", "main", "high")
        and 0 < width <= MAX_WIDTH and 0 < height <= MAX_HEIGHT
        and 0 < fps <= MAX_FPS + 0.05
        and (level == 0 or level <= MAX_LEVEL)
    )


def normalize_video(source: Path, output: Path) -> str:
    """Create complete faststart MP4; return 'copy' or 'transcode'."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("ffmpeg не установлен: sudo apt install -y ffmpeg")
    stream = probe_video(source)
    copy = can_stream_copy(stream)
    common = [
        "nice", "-n", "15", ffmpeg, "-hide_banner", "-loglevel", "error",
        "-nostdin", "-y", "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
    ]
    if copy:
        encoder = ["-c:v", "copy"]
    else:
        width = int(stream["width"])
        height = int(stream["height"])
        scale = min(1.0, MAX_WIDTH / width, MAX_HEIGHT / height)
        out_width = max(2, int(width * scale) // 2 * 2)
        out_height = max(2, int(height * scale) // 2 * 2)
        # CPU-safe fallback: two low-priority software-encoding threads.
        # Encoding 4K originals can take a long time on a Pi 4.
        encoder = [
            "-vf", f"fps={MAX_FPS},scale={out_width}:{out_height}:flags=fast_bilinear,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
            "-threads", "2", "-profile:v", "main", "-level:v", "4.0",
            "-maxrate", "4500k", "-bufsize", "9000k",
        ]
    command = common + encoder + ["-movflags", "+faststart", str(output)]
    subprocess.run(command, check=True, timeout=300 if copy else 1200,
                   capture_output=True)
    with output.open("rb") as result:
        header = result.read(12)
    if header[4:8] != b"ftyp" or header[8:12] == b"qt  ":
        raise ValueError("ffmpeg не создал совместимый MP4")
    if not 1024 <= output.stat().st_size <= MAX_BYTES:
        raise ValueError("Готовое видео пустое или слишком большое")
    # Verify output itself; copy mode must not silently preserve unsupported codecs.
    verified = probe_video(output)
    if not can_stream_copy(verified):
        raise ValueError("Готовый MP4 не подходит профилю Pi 4")
    return "copy" if copy else "transcode"
