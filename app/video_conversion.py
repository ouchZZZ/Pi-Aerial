"""Prepare video for Raspberry Pi 4's reliable HTML5 H.264/MP4 playback.

Use lossless remux for compatible 8-bit AVC, otherwise transcode in a
low-priority, single-worker background job. Never replace playable cache until
the entire output is verified.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections import deque
from fractions import Fraction
from pathlib import Path

MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_FPS = 30
MAX_LEVEL = 42
# One ffmpeg normalization at a time across online and manual uploads.
CONVERSION_SLOT = threading.BoundedSemaphore(1)


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


def _emit(callback, **fields) -> None:
    if callback is None:
        return
    try:
        callback(**fields)
    except Exception:
        # UI telemetry must never interrupt the real video pipeline.
        pass


def probe_duration(source: Path) -> float | None:
    """Return duration when ffprobe reports a finite positive number."""
    import math
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(source)],
            check=True, text=True, capture_output=True, timeout=30,
        )
        duration = float(json.loads(result.stdout).get("format", {}).get("duration"))
        return duration if math.isfinite(duration) and 0 < duration < 24 * 3600 else None
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        return None


def _parse_out_time(fields: dict) -> float | None:
    """ffmpeg's out_time_ms is historically measured in microseconds."""
    for key in ("out_time_us", "out_time_ms"):
        if key in fields:
            try:
                return max(0.0, float(fields[key]) / 1_000_000)
            except (TypeError, ValueError):
                pass
    raw = fields.get("out_time")
    if raw:
        try:
            hours, minutes, seconds = raw.split(":")
            return max(0.0, 3600 * int(hours) + 60 * int(minutes) + float(seconds))
        except (ValueError, TypeError):
            pass
    return None


def _run_with_progress(command: list[str], timeout: int,
                       callback, duration: float | None,
                       stop: threading.Event | None = None) -> None:
    """Stop after two consecutive idle windows, never for total runtime."""
    args = command[:-1] + ["-progress", "pipe:1", "-nostats", command[-1]]
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf8", errors="replace", bufsize=1,
    )
    tail = deque(maxlen=8)
    progress_lock = threading.Lock()
    last_advance = time.monotonic()
    high_water = {}

    def advance(fields) -> None:
        nonlocal last_advance
        metrics = {"position": _parse_out_time(fields)}
        for key in ("frame", "total_size"):
            try:
                metrics[key] = max(0, int(fields.get(key, "")))
            except (TypeError, ValueError):
                pass
        with progress_lock:
            moved = False
            for key, value in metrics.items():
                if value is not None and value > high_water.get(key, 0):
                    high_water[key] = value
                    moved = True
            if moved:
                last_advance = time.monotonic()

    def consume() -> None:
        fields = {}
        try:
            for line in process.stdout:
                line = line.strip()
                if "=" not in line:
                    if line:
                        tail.append(line[:180])
                    continue
                name, value = line.split("=", 1)
                if name in ("out_time_us", "out_time_ms", "out_time", "speed", "frame", "total_size"):
                    fields[name] = value
                if name == "progress":
                    advance(fields)
                    _emit(callback, stage="converting",
                          position_s=_parse_out_time(fields),
                          duration_s=duration, encode_speed=fields.get("speed"))
                    fields.clear()
        finally:
            process.stdout.close()

    reader = threading.Thread(target=consume, name="ffmpeg-progress", daemon=True)
    reader.start()
    try:
        warned_at = None
        while True:
            if stop is not None and stop.is_set():
                process.kill()
                process.wait(timeout=10)
                raise InterruptedError("Конвертация остановлена")
            try:
                code = process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                with progress_lock:
                    idle = now - last_advance
                    advanced_at = last_advance
                if warned_at is not None and advanced_at > warned_at:
                    warned_at = None
                if idle < timeout:
                    warned_at = None
                elif warned_at is None:
                    warned_at = now
                    print("[conversion] Нет прогресса 20 минут; повторная проверка через 20 минут",
                          flush=True)
                elif now - warned_at >= timeout:
                    process.kill()
                    process.wait(timeout=10)
                    raise TimeoutError(
                        "ffmpeg: нет прогресса в двух последовательных интервалах по 20 минут"
                    ) from None
    finally:
        reader.join(timeout=3)
    if code:
        message = " | ".join(tail)[-400:]
        raise RuntimeError("ffmpeg завершился с кодом " + str(code)
                           + (": " + message if message else ""))


def normalize_video(source: Path, output: Path, max_bytes: int | None = None,
                    on_progress=None, *, force_transcode: bool = False,
                    stop: threading.Event | None = None) -> str:
    """Serialize conversion; background tasks can cancel while waiting."""
    _emit(on_progress, stage="waiting")
    if stop is None:
        with CONVERSION_SLOT:
            return _normalize_video_locked(source, output, max_bytes, on_progress,
                                           force_transcode=force_transcode)
    while not stop.is_set():
        if CONVERSION_SLOT.acquire(timeout=0.5):
            try:
                if stop.is_set():
                    raise InterruptedError("Конвертация остановлена")
                return _normalize_video_locked(source, output, max_bytes, on_progress,
                                               force_transcode=force_transcode, stop=stop)
            finally:
                CONVERSION_SLOT.release()
    raise InterruptedError("Конвертация остановлена")


def _normalize_video_locked(source: Path, output: Path, max_bytes: int | None,
                            on_progress=None, *, force_transcode=False,
                            stop: threading.Event | None = None) -> str:
    """Create complete faststart MP4; return 'copy' or 'transcode'."""
    if stop is not None and stop.is_set():
        raise InterruptedError("Конвертация остановлена")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("ffmpeg не установлен: sudo apt install -y ffmpeg")
    stream = probe_video(source)
    copy = not force_transcode and can_stream_copy(stream)
    common = [
        "nice", "-n", "15", ffmpeg, "-hide_banner", "-loglevel", "error",
        "-nostdin", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
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
    # Preserve the first audio track when present. AAC is widely supported by
    # Chromium in MP4, while the '?' keeps silent Apple/NASA clips valid.
    command = common + encoder + [
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", str(output),
    ]
    duration = probe_duration(source)
    _emit(on_progress, stage="converting",
          mode="copy" if copy else "transcode", duration_s=duration)
    if stop is None:
        _run_with_progress(command, 1200, on_progress, duration)
    else:
        if stop.is_set():
            raise InterruptedError("Конвертация остановлена")
        _run_with_progress(command, 1200, on_progress, duration, stop=stop)
        if stop.is_set():
            raise InterruptedError("Конвертация остановлена")
    _emit(on_progress, stage="finalizing", mode="copy" if copy else "transcode")
    with output.open("rb") as result:
        header = result.read(12)
    if header[4:8] != b"ftyp" or header[8:12] == b"qt  ":
        raise ValueError("ffmpeg не создал совместимый MP4")
    if output.stat().st_size < 1024 or (
            max_bytes is not None and output.stat().st_size > max_bytes):
        raise ValueError("Готовое видео пустое или слишком большое")
    # Verify output itself; copy mode must not silently preserve unsupported codecs.
    verified = probe_video(output)
    if not can_stream_copy(verified):
        raise ValueError("Готовый MP4 не подходит профилю Pi 4")
    return "copy" if copy else "transcode"
