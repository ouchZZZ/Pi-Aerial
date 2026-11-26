"""Regressions for Pi 4 Chromium video-decoder workaround."""
import subprocess
from pathlib import Path

from app import kiosk


STOCK = """#!/usr/bin/env bash
set -u
command -v chromium >/dev/null && BROWSER=chromium || BROWSER=chromium-browser
while true; do
  "$BROWSER" --kiosk --incognito --noerrdialogs --no-first-run --disable-infobars \\
    --disable-session-crashed-bubble --disable-features=TranslateUI \\
    --deny-permission-prompts --autoplay-policy=no-user-gesture-required --password-store=basic \\
    http://127.0.0.1:8080
  sleep 4
done
"""


def test_migrates_existing_launcher_once_without_losing_flags(tmp_path):
    script = tmp_path / "kiosk.sh"
    script.write_text(STOCK, encoding="utf-8")
    script.chmod(0o750)
    assert kiosk.enable_hardware_video_decode(script) is True
    updated = script.read_text(encoding="utf-8")
    assert kiosk.MARKER in updated
    assert updated.count("--disable-accelerated-video-decode") == 1
    assert '"${VIDEO_DECODE_FLAGS[@]}" http://127.0.0.1:8080' in updated
    assert "--deny-permission-prompts" in updated
    assert "--disable-features=TranslateUI" in updated
    assert script.stat().st_mode & 0o777 == 0o750
    assert kiosk.enable_hardware_video_decode(script) is False
    assert script.read_text(encoding="utf-8") == updated
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_custom_or_symlinked_launcher_is_untouched(tmp_path):
    custom = tmp_path / "custom.sh"
    custom.write_text("#!/bin/bash\nexec chromium --kiosk https://example.com\n", encoding="utf-8")
    assert kiosk.enable_hardware_video_decode(custom) is False
    assert custom.read_text(encoding="utf-8").startswith("#!/bin/bash")
    link = tmp_path / "kiosk.sh"
    link.symlink_to(custom)
    assert kiosk.enable_hardware_video_decode(link) is False
    assert link.is_symlink()


def test_installer_and_migrated_launchers_have_the_same_policy(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")
    launcher = source.split("<<'KIOSK'\n", 1)[1].split("\nKIOSK\n", 1)[0]
    script = tmp_path / "new-kiosk.sh"
    script.write_text(launcher, encoding="utf-8")
    assert kiosk.MARKER in launcher
    assert '"${VIDEO_DECODE_FLAGS[@]}" http://127.0.0.1:8080' in launcher
    assert "data/force-software-video-decode" in launcher
    assert kiosk.enable_hardware_video_decode(script) is False
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_legacy_launcher_without_permission_flag_migrates(tmp_path):
    script = tmp_path / "kiosk.sh"
    script.write_text(STOCK.replace("--deny-permission-prompts ", ""), encoding="utf-8")
    assert kiosk.enable_hardware_video_decode(script) is True
    assert "--disable-accelerated-video-decode" in script.read_text(encoding="utf-8")


def test_migrates_v1_launcher_to_hardware_default(tmp_path):
    script = tmp_path / "kiosk.sh"
    legacy = STOCK.replace("while true; do", kiosk.LEGACY_FLAGS + "while true; do", 1)
    legacy = legacy.replace("    http://127.0.0.1:8080", '    "${VIDEO_DECODE_FLAGS[@]}" http://127.0.0.1:8080')
    script.write_text(legacy, encoding="utf-8")
    assert kiosk.enable_hardware_video_decode(script) is True
    updated = script.read_text(encoding="utf-8")
    assert kiosk.LEGACY_FLAGS not in updated
    assert kiosk.FLAGS in updated
    assert 'if [[ -e ' in updated
    assert "data/force-software-video-decode" in updated
    assert kiosk.enable_hardware_video_decode(script) is False
    subprocess.run(["bash", "-n", str(script)], check=True)
