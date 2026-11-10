#!/usr/bin/env bash
# Pi Aerial setup for Raspberry Pi OS Desktop using labwc.
set -euo pipefail
if [[ "${EUID}" -eq 0 ]]; then echo 'Запускайте от обычного пользователя, не root.'; exit 1; fi
HERE="$(cd "$(dirname "$0")" && pwd)"

# Bootstrap/update only our fixed Raspberry Pi OS package whitelist.
# The helper is copied into a root-owned path for restricted web-initiated updates.
if [[ "${PI_AERIAL_SKIP_SYSTEM_DEPS:-0}" != "1" ]]; then
  command -v sudo >/dev/null || { echo 'Установите sudo (на Raspberry Pi OS Desktop он уже есть).'; exit 1; }
  echo '[deps] Проверяю системные пакеты Raspberry Pi OS...'
  sudo bash "$HERE/tools/system_deps.sh" --refresh

  HELPER=/usr/local/libexec/pi-aerial/system_deps
  sudo install -D -o root -g root -m 0755 "$HERE/tools/system_deps.sh" "$HELPER"
  SUDOERS_TMP="$(mktemp)"
  trap 'rm -f "$SUDOERS_TMP"' EXIT
  # Grant *only* the static helper with --ensure; never apt, shell, or the repo.
  printf '%s ALL=(root) NOPASSWD: %s --ensure\n' "$(id -un)" "$HELPER" > "$SUDOERS_TMP"
  chmod 0600 "$SUDOERS_TMP"
  sudo visudo -cf "$SUDOERS_TMP" >/dev/null
  sudo install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/pi-aerial-system-deps
  rm -f "$SUDOERS_TMP"
  trap - EXIT
  echo '[deps] Привилегированный помощник зарегистрирован; веб-панель может доставлять недостающие пакеты.'
else
  echo '[deps] Проверка системных пакетов пропущена (PI_AERIAL_SKIP_SYSTEM_DEPS=1).'
fi

BASE="${PI_AERIAL_INSTALL_DIR:-$HOME/pi-aerial}"
VERSION="$(cat "$HERE/VERSION")"
GIT_REVISION=""
if [[ -d "$HERE/.git" ]]; then
  GIT_REVISION="$(git -C "$HERE" rev-parse HEAD)"
fi
RELEASE_ID="$VERSION"
if [[ -n "$GIT_REVISION" ]]; then RELEASE_ID="$VERSION-g${GIT_REVISION:0:12}"; fi
echo "Pi Aerial $VERSION → $BASE"
command -v python3 >/dev/null || { echo 'python3 не найден после проверки зависимостей'; exit 1; }
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
  echo 'Chromium не найден после проверки зависимостей'; exit 1
fi
mkdir -p "$BASE/releases" "$BASE/data/videos" "$BASE/data/modules" "$BASE/data/trusted_keys" "$BASE/data/incoming" "$BASE/data/weather_cache"
DEST="$BASE/releases/$RELEASE_ID"
if [[ ! -e "$DEST" ]]; then
  mkdir -p "$DEST"
  cp -a "$HERE/app" "$HERE/web" "$HERE/tools" "$HERE/requirements.txt" "$HERE/VERSION" "$DEST/"
  if [[ -n "$GIT_REVISION" ]]; then printf "%s\n" "$GIT_REVISION" > "$DEST/GIT_REVISION"; fi
else
  echo "Релиз $VERSION уже установлен; не перезаписываю файлы."
fi
if [[ ! -L "$BASE/current" ]]; then ln -s "$DEST" "$BASE/current"; fi
if [[ ! -d "$DEST/.venv" ]]; then
  python3 -m venv "$DEST/.venv" || { echo 'Не удалось создать venv. Установите: sudo apt install python3-venv'; exit 1; }
fi
"$DEST/.venv/bin/python" -m pip install -r "$DEST/requirements.txt"
if [[ -n "$GIT_REVISION" ]]; then
  test -f "$HOME/.ssh/pi_aerial_github" || { echo 'Deploy key ~/.ssh/pi_aerial_github не найден'; exit 1; }
  printf '{"enabled":true,"remote":"git@github.com:ouchZZZ/Pi-Aerial.git","ssh_key":"%s/.ssh/pi_aerial_github"}\n' "$HOME" > "$BASE/data/github.json"
  chmod 600 "$BASE/data/github.json"
fi
# The updater is independent of the releases it replaces.
cp "$HERE/tools/apply_core_update.py" "$BASE/update_helper.py"
chmod 700 "$BASE/update_helper.py"
chmod 700 "$BASE/data"
if [[ ! -s "$BASE/data/admin_auth.json" ]]; then
  (cd "$BASE/current" && PI_AERIAL_DATA_DIR="$BASE/data" PYTHONPATH="$BASE/current" "$BASE/current/.venv/bin/python" tools/set_password.py)
fi
mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/labwc"
cat > "$HOME/.config/systemd/user/pi-aerial.service" <<SERVICE
[Unit]
Description=Pi Aerial screen and administration
After=network.target
[Service]
Type=simple
WorkingDirectory=$BASE/current
Environment=PI_AERIAL_BASE=$BASE
Environment=PI_AERIAL_DATA_DIR=$BASE/data
ExecStart=$BASE/current/.venv/bin/python -m uvicorn app.main:app --app-dir $BASE/current --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true
[Install]
WantedBy=default.target
SERVICE
cat > "$HOME/.config/systemd/user/pi-aerial-updater.service" <<SERVICE
[Unit]
Description=Safe Pi Aerial release installer and rollback helper
[Service]
Type=oneshot
Environment=PI_AERIAL_BASE=$BASE
ExecStart=$BASE/current/.venv/bin/python $BASE/update_helper.py
TimeoutStartSec=600
SERVICE
cat > "$HOME/.config/systemd/user/pi-aerial-updater.path" <<SERVICE
[Unit]
Description=Watch for Pi Aerial core update requests
[Path]
PathExists=$BASE/data/pending_update.json
[Install]
WantedBy=default.target
SERVICE
cat > "$BASE/kiosk.sh" <<'KIOSK'
#!/usr/bin/env bash
set -u
command -v chromium >/dev/null && BROWSER=chromium || BROWSER=chromium-browser
while true; do
  "$BROWSER" --kiosk --incognito --noerrdialogs --no-first-run --disable-infobars \
    --disable-session-crashed-bubble --disable-features=TranslateUI \
    --autoplay-policy=no-user-gesture-required --password-store=basic \
    http://127.0.0.1:8080
  sleep 4
done
KIOSK
chmod +x "$BASE/kiosk.sh"
AUTO="$HOME/.config/labwc/autostart"
touch "$AUTO"
if ! grep -Fq '# PI_AERIAL_KIOSK' "$AUTO"; then
  printf '\n# PI_AERIAL_KIOSK\n%s &\n' "$BASE/kiosk.sh" >> "$AUTO"
fi
systemctl --user daemon-reload
systemctl --user enable --now pi-aerial.service pi-aerial-updater.path
if command -v sudo >/dev/null; then
  sudo loginctl enable-linger "$(id -un)" || echo 'Внимание: enable-linger не выполнен, проверьте запуск службы после загрузки.'
fi
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo '================ Pi Aerial ================'
echo 'Панель: http://'"${IP:-raspberrypi.local}"':8080/admin'
echo 'Экран:  http://localhost:8080/'
echo 'Логи:   journalctl --user -u pi-aerial -f'
if [[ -n "$GIT_REVISION" ]]; then echo "GitHub: подключён read-only источник (${GIT_REVISION:0:12})"; fi
echo 'Служба: systemctl --user status pi-aerial'
echo 'Включите Desktop Autologin через sudo raspi-config (System Options > Boot / Auto Login).'
echo 'После этого перезагрузите Pi: sudo reboot'
echo '==========================================='
