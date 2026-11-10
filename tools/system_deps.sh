#!/usr/bin/env bash
# Restricted, root-owned package helper for Raspberry Pi OS (Debian/apt).
# Keep this whitelist static: no package names, paths, or commands from the web UI.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
packages=(
  ca-certificates
  chromium
  curl
  ffmpeg
  git
  labwc
  openssh-client
  python3
  python3-pip
  python3-venv
  sudo
  tzdata
)

case "${1:-}" in
  --list)
    if (( $# != 1 )); then echo "Unexpected arguments" >&2; exit 2; fi
    printf '%s\n' "${packages[@]}"
    exit 0
    ;;
  --ensure|--refresh)
    if (( $# != 1 )); then echo "Unexpected arguments" >&2; exit 2; fi
    ;;
  *)
    echo "Usage: system_deps [--list|--ensure|--refresh]" >&2
    exit 2
    ;;
esac

if (( EUID != 0 )); then
  echo "System packages require root; run with sudo." >&2
  exit 1
fi
if ! command -v apt-get >/dev/null || ! command -v dpkg-query >/dev/null; then
  echo "Only apt-based Raspberry Pi OS / Debian is supported." >&2
  exit 1
fi

missing=()
for package in "${packages[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    missing+=("$package")
  fi
done

if [[ $1 == --ensure && ${#missing[@]} -eq 0 ]]; then
  echo "[deps] All required system packages are already installed."
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
echo "[deps] Refreshing apt metadata..."
apt-get -o DPkg::Lock::Timeout=120 update -qq
if [[ $1 == --refresh ]]; then
  echo "[deps] Installing/updating only Pi Aerial packages: ${packages[*]}"
  apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "${packages[@]}"
else
  echo "[deps] Installing missing packages: ${missing[*]}"
  apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "${missing[@]}"
fi
