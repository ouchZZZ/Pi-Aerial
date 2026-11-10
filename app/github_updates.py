"""Read-only GitHub source for a private Pi-Aerial repository via SSH deploy key.

Only the fixed, locally configured repository and main branch are accepted.
No URL, branch, or shell command comes from a browser request.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import bundles
from .storage import BASE, DATA, read_json

EXPECTED_REPOSITORY = 'git@github.com:ouchZZZ/Pi-Aerial.git'
SHA_PATTERN = re.compile(r'[a-f0-9]{40}\Z')


def _config():
    config = read_json(DATA / 'github.json')
    if not config.get('enabled'):
        raise ValueError('GitHub не настроен: установите приложение из Git-клона через install.sh')
    remote = config.get('remote')
    if remote != EXPECTED_REPOSITORY:
        raise ValueError('Некорректный GitHub-репозиторий в конфигурации')
    key = Path(config.get('ssh_key', '')).expanduser()
    if not key.is_file() or key.is_symlink():
        raise ValueError('SSH deploy key не найден')
    return remote, key


def _git_env(key: Path):
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_SSH_COMMAND'] = (
        f'ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes '
        '-o StrictHostKeyChecking=yes -o ConnectTimeout=10'
    )
    return env


def _run_git(arguments, timeout=55):
    remote, key = _config()
    try:
        command = subprocess.run(
            ['git', *arguments], env=_git_env(key), capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'GitHub недоступен: {exc}') from exc
    if command.returncode:
        raise ValueError(f'Ошибка доступа к GitHub: {command.stderr.strip()[:350]}')
    return command.stdout


def remote_revision():
    remote, _ = _config()
    line = _run_git(['ls-remote', remote, 'refs/heads/main'], timeout=25).strip()
    sha = line.split('\t')[0] if line else ''
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError('Не удалось получить SHA ветки main')
    return sha


def installed_revision():
    return (BASE / 'current' / 'GIT_REVISION').read_text().strip() if (BASE / 'current' / 'GIT_REVISION').is_file() else None


def status():
    current = installed_revision()
    latest = remote_revision()
    return {'repository': 'ouchZZZ/Pi-Aerial', 'branch': 'main',
            'installed': current, 'latest': latest, 'update_available': current != latest}


@contextmanager
def checkout(expected_revision: str | None = None):
    """Temporary shallow checkout. Reject TOCTOU changes to main during an update."""
    remote, _ = _config()
    with tempfile.TemporaryDirectory(prefix='pi-aerial-github-', dir=DATA / 'incoming') as temp:
        dest = Path(temp) / 'repo'
        _run_git(['clone', '--depth', '1', '--single-branch', '--branch', 'main', '--', remote, str(dest)], timeout=120)
        result = subprocess.run(['git', '-C', str(dest), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, check=True, timeout=10)
        sha = result.stdout.strip()
        if not SHA_PATTERN.fullmatch(sha):
            raise ValueError('Неверный SHA клонированного репозитория')
        if expected_revision and sha != expected_revision:
            raise ValueError('Ветка main изменилась после проверки; проверьте обновления снова')
        yield dest, sha


def catalog():
    with checkout() as (repo, sha):
        root = repo / 'catalog'
        entries = []
        for path in sorted(root.iterdir()) if root.is_dir() else []:
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                manifest = bundles.validate_source_module(path)
            except (ValueError, OSError):
                continue
            installed = read_json(DATA / 'modules' / manifest['id'] / 'current' / 'manifest.json')
            entries.append({'id': manifest['id'], 'name': manifest.get('name', manifest['id']),
                            'description': manifest.get('description', ''),
                            'version': manifest['version'], 'installed_version': installed.get('version')})
        return {'revision': sha, 'modules': entries}


def install_from_catalog(module_id: str):
    if not bundles.safe_id(module_id):
        raise ValueError('Неверный идентификатор модуля')
    with checkout() as (repo, sha):
        source = repo / 'catalog' / module_id
        manifest = bundles.validate_source_module(source)
        if manifest['id'] != module_id:
            raise ValueError('Неверный идентификатор в manifest.json')
        return bundles.install_module_directory(source), sha
