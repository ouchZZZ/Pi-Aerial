"""Raspberry Pi 4 video conversion: no quality loss when compatible, safe fallback."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import video_conversion as vc


AVC = {
    "codec_name": "h264", "pix_fmt": "yuv420p",
    "profile": "High", "level": 40,
    "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
}


def test_can_remux_without_encoding():
    assert vc.can_stream_copy(AVC)
    assert vc.can_stream_copy({**AVC, "width": 1280, "height": 720,
                               "avg_frame_rate": "24000/1001"})
    assert not vc.can_stream_copy({**AVC, "codec_name": "hevc"})
    assert not vc.can_stream_copy({**AVC, "pix_fmt": "yuv420p10le"})
    assert not vc.can_stream_copy({**AVC, "profile": "High 10"})
    assert not vc.can_stream_copy({**AVC, "width": 3840, "height": 2160})
    assert not vc.can_stream_copy({**AVC, "avg_frame_rate": "60/1"})
    assert not vc.can_stream_copy({**AVC, "level": 51})


def _fake_convert(monkeypatch, tmp_path, src_profile):
    original = tmp_path / "source.part"
    original.write_bytes(b"source video")
    output = tmp_path / "normalized.mp4"
    recorded = []
    monkeypatch.setattr(vc.shutil, "which",
                        lambda command: "/usr/bin/" + command)
    monkeypatch.setattr(vc, "probe_video",
                        lambda file: src_profile if file == original else AVC)

    def run(command, **kwargs):
        recorded.append((command, kwargs))
        if command[0] == "nice":
            output.write_bytes(
                bytes.fromhex("000000186674797069736f6d") + b"A" * 2500
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(vc, "probe_duration", lambda source: None)
    monkeypatch.setattr(vc, "_run_with_progress",
                        lambda command, timeout, callback, duration: run(command, timeout=timeout))
    return original, output, recorded


def test_copy_h264_in_background_without_quality_loss(monkeypatch, tmp_path):
    src, dest, calls = _fake_convert(monkeypatch, tmp_path, AVC)
    assert vc.normalize_video(src, dest) == "copy"
    command, kwargs = calls[0]
    assert command[:3] == ["nice", "-n", "15"]
    assert command[command.index("-c:v") + 1] == "copy"
    assert "+faststart" in command
    assert kwargs["timeout"] == 1200


def test_force_transcode_even_compatible_1080_h264(monkeypatch, tmp_path):
    src, dest, calls = _fake_convert(monkeypatch, tmp_path, AVC)
    assert vc.normalize_video(src, dest, force_transcode=True) == "transcode"
    command, kwargs = calls[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert "scale=1920:1080" in command[command.index("-vf") + 1]
    assert kwargs["timeout"] == 1200


def test_transcode_hevc_with_two_threads_and_scaled_1080p(monkeypatch, tmp_path):
    hevc = {**AVC, "codec_name": "hevc", "profile": "Main 10",
            "pix_fmt": "yuv420p10le", "width": 3840, "height": 2160,
            "avg_frame_rate": "60000/1001"}
    src, dest, calls = _fake_convert(monkeypatch, tmp_path, hevc)
    assert vc.normalize_video(src, dest) == "transcode"
    command, kwargs = calls[0]
    assert command[:3] == ["nice", "-n", "15"]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-threads") + 1] == "2"
    assert "scale=1920:1080" in command[command.index("-vf") + 1]
    assert "format=yuv420p" in command[command.index("-vf") + 1]
    assert kwargs["timeout"] == 1200


def test_transcoding_does_not_upscale_small_videos(monkeypatch, tmp_path):
    vp9 = {**AVC, "codec_name": "vp9", "width": 854, "height": 480}
    src, dest, calls = _fake_convert(monkeypatch, tmp_path, vp9)
    assert vc.normalize_video(src, dest) == "transcode"
    command, _ = calls[0]
    assert "scale=854:480" in command[command.index("-vf") + 1]


def test_normalization_requires_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(vc.shutil, "which", lambda command: None)
    with pytest.raises(ValueError, match="ffmpeg"):
        vc.normalize_video(tmp_path / "in.part", tmp_path / "out.mp4")
