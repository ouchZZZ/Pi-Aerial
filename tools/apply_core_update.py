#!/usr/bin/env python3
"""Stable user-service update helper; verifies new release then rolls back on failure."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(os.environ.get('PI_AERIAL_BASE', Path.home() / 'pi-aerial')).resolve()
DATA = BASE / 'data'
PENDING = DATA / 'pending_update.json'
STATUS = DATA / 'update_status.json'
VERSION_RX = re.compile(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?\Z')
SHA_RX = re.compile(r'[0-9a-f]{40}\Z')
RELEASE_FILES = ('app', 'web', 'tools', 'requirements.txt', 'VERSION')
SYSTEM_DEPS_HELPER = Path('/usr/local/libexec/pi-aerial/system_deps')


def ensure_system_dependencies():
    """Use a root-owned fixed whitelist; never pass package names from GitHub."""
    if not SYSTEM_DEPS_HELPER.is_file() or SYSTEM_DEPS_HELPER.is_symlink():
        # Older 0.2.0 installs predate the restricted helper; they remain upgradeable.
        # Re-run install.sh once after updating to enable unattended OS dependency repair.
        print('[deps] Root helper missing: re-run the new install.sh once to enable automatic OS package repair.',
              file=sys.stderr, flush=True)
        return
    subprocess.run(['sudo', '-n', str(SYSTEM_DEPS_HELPER), '--ensure'],
                   check=True, timeout=420)


def save_status(message, ok):
    tmp = STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps({'ok': ok, 'message': message, 'at': time.time()}, ensure_ascii=False))
    os.replace(tmp, STATUS)


def switch(target):
    active = BASE / 'current'
    temp = BASE / ('.current-new-' + str(os.getpid()))
    try:
        temp.symlink_to(target, target_is_directory=True)
        os.replace(temp, active)
    finally:
        temp.unlink(missing_ok=True)


def service_restart():
    subprocess.run(['systemctl', '--user', 'restart', 'pi-aerial.service'], check=True, timeout=35)


def healthy(version, revision=None):
    for _ in range(45):
        time.sleep(1)
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2) as r:
                response = json.loads(r.read())
            if response.get('ok') and response.get('version') == version and (revision is None or response.get('revision') == revision):
                return True
        except Exception:
            pass
    return False


def prepare_venv(target, old):
    """Each release gets its own dependencies so rollback does not reuse a changed venv."""
    requirements = target / 'requirements.txt'
    if not requirements.is_file():
        shutil.copy2(old / 'requirements.txt', requirements)
    if not (target / '.venv' / 'bin' / 'python').exists():
        subprocess.run([sys.executable, '-m', 'venv', str(target / '.venv')], check=True, timeout=90)
    # Always retry pip: an interrupted previous download may leave a half-built venv.
    subprocess.run([str(target / '.venv' / 'bin' / 'python'), '-m', 'pip',
                    'install', '--disable-pip-version-check', '-r', str(requirements)],
                   check=True, timeout=360)


def stage_github(queued, old):
    revision = queued.get('revision')
    if not isinstance(revision, str) or not SHA_RX.fullmatch(revision):
        raise ValueError('Неверный SHA обновления GitHub')
    # Import the Git client from the *old* trusted release, not from the new checkout.
    sys.path.insert(0, str(old))
    from app import github_updates
    with github_updates.checkout(expected_revision=revision) as (repo, sha):
        version = (repo / 'VERSION').read_text().strip()
        if not VERSION_RX.fullmatch(version):
            raise ValueError('Недопустимая версия VERSION')
        release = f'{version}-g{sha[:12]}'
        target = BASE / 'releases' / release
        if target.exists():
            if (target / 'GIT_REVISION').read_text().strip() != sha:
                raise ValueError('Конфликт каталога выпуска')
        else:
            target.mkdir(parents=True)
            try:
                for name in RELEASE_FILES:
                    source = repo / name
                    if source.is_symlink() or not source.exists():
                        raise ValueError(f'Отсутствует или небезопасен файл {name}')
                    if source.is_dir():
                        if any(p.is_symlink() for p in source.rglob('*')):
                            raise ValueError('Символические ссылки в пакете ядра запрещены')
                        shutil.copytree(source, target / name, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
                    else:
                        shutil.copy2(source, target / name)
                (target / 'GIT_REVISION').write_text(sha + '\n')
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
        return target, version, sha



def refresh_helper(target):
    """Upgrade the stable updater only after the new release passes its health check."""
    candidate = target / 'tools' / 'apply_core_update.py'
    if not candidate.is_file() or candidate.is_symlink():
        return
    compile(candidate.read_text(encoding='utf8'), str(candidate), 'exec')
    active = BASE / 'update_helper.py'
    tmp = BASE / '.update_helper.new'
    backup = BASE / 'update_helper.previous.py'
    shutil.copy2(candidate, tmp)
    if active.exists():
        shutil.copy2(active, backup)
    os.replace(tmp, active)
    active.chmod(0o700)

def main():
    if not PENDING.exists():
        return
    old = (BASE / 'current').resolve(strict=True)
    did_switch = False
    try:
        queued = json.loads(PENDING.read_text())
        if queued.get('source') == 'github':
            target, version, revision = stage_github(queued, old)
        else:
            version = queued['version']
            revision = None
            if not isinstance(version, str) or not VERSION_RX.fullmatch(version):
                raise ValueError('Недопустимая версия')
            target = BASE / 'releases' / version
        if not target.is_dir() or target.is_symlink():
            raise ValueError('Выпуск не найден')
        if (target / 'VERSION').read_text().strip() != version:
            raise ValueError('Версия выпуска не совпадает')
        if target.resolve() == old:
            save_status('Последняя версия уже установлена', True)
            return
        ensure_system_dependencies()
        prepare_venv(target, old)
        switch(target)
        did_switch = True
        service_restart()
        if not healthy(version, revision):
            raise RuntimeError('Новая версия не прошла проверку состояния')
        refresh_helper(target)
        save_status(f'Установлена версия {version}' + (f' ({revision[:12]})' if revision else ''), True)
    except Exception as exc:
        if did_switch:
            switch(old)
            try:
                service_restart()
                save_status(f'Неудачное обновление: восстановлена предыдущая версия ({exc})', False)
            except Exception as restart_exc:
                save_status(f'Ошибка отката: {restart_exc}', False)
        else:
            save_status(f'Ошибка подготовки обновления: {exc}', False)
        raise
    finally:
        PENDING.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
