"""One-time, conservative migration of the installed Chromium kiosk launcher.

The kiosk script lives outside versioned releases. Updating the web application
does not replace it, so existing installations need an idempotent migration.
Only recognize the stock Chromium launch shape; never replace custom scripts.
The running kiosk picks up the new arguments after the next desktop login/reboot.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

MARKER = "# PI_AERIAL_HARDWARE_VIDEO_DECODE_V2"
LEGACY_FLAGS = """# PI_AERIAL_SOFTWARE_VIDEO_DECODE_V1
# Touch data/allow-hardware-video-decode and restart kiosk to opt back in.
VIDEO_DECODE_FLAGS=()
if [[ ! -e "$(dirname -- "${BASH_SOURCE[0]}")/data/allow-hardware-video-decode" ]]; then
  VIDEO_DECODE_FLAGS=(--disable-accelerated-video-decode)
fi
"""
FLAGS = """# PI_AERIAL_HARDWARE_VIDEO_DECODE_V2
# Touch data/force-software-video-decode and restart kiosk if hardware decoding is unstable.
VIDEO_DECODE_FLAGS=()
if [[ -e "$(dirname -- "${BASH_SOURCE[0]}")/data/force-software-video-decode" ]]; then
  VIDEO_DECODE_FLAGS=(--disable-accelerated-video-decode)
fi
"""
URL_LINE = re.compile(r"(?m)^([ \t]+)http://127\.0\.0\.1:8080[ \t]*$")


def enable_hardware_video_decode(script: Path) -> bool:
    """Patch a known stock launcher once, without clobbering user edits.

    Returns True only when an actual modification was written. A custom
    launcher with an unknown command layout is intentionally left untouched.
    """
    if script.is_symlink() or not script.is_file():
        return False
    original = script.read_text(encoding="utf-8")
    if MARKER in original:
        return False
    if LEGACY_FLAGS in original:
        updated = original.replace(LEGACY_FLAGS, FLAGS, 1)
        return _write_launcher(script, updated)
    if "--disable-accelerated-video-decode" in original:
        return False
    if (original.count("while true; do") != 1
            or original.count('"$BROWSER" --kiosk') != 1
            or "--password-store=basic" not in original):
        return False
    matches = list(URL_LINE.finditer(original))
    if len(matches) != 1:
        return False
    updated = original.replace("while true; do", FLAGS + "while true; do", 1)
    updated, count = URL_LINE.subn(
        lambda match: match.group(1) + '"${VIDEO_DECODE_FLAGS[@]}" http://127.0.0.1:8080',
        updated,
        count=1,
    )
    if count != 1:
        return False
    return _write_launcher(script, updated)


def _write_launcher(script: Path, updated: str) -> bool:
    tmp = script.with_name(f".{script.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(updated, encoding="utf-8")
        tmp.chmod(script.stat().st_mode & 0o777)
        os.replace(tmp, script)
    finally:
        tmp.unlink(missing_ok=True)
    return True
