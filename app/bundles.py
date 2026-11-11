"""Ed25519-signed module/core ZIP verification, traversal-safe staging and rollback."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from threading import RLock
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from . import update_control
from .storage import BASE, DATA, CURRENT_VERSION, LOCK, atomic_json, read_json, safe_id

BUNDLE_LOCK = RLock()
MAX_BUNDLE_BYTES = 80 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_FILES = 200
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?\Z')


def signed_payload(z: zipfile.ZipFile) -> bytes:
    """Canonical bytes to sign. Path + SHA256 digest for all files except SIGNATURE."""
    records = []
    for info in z.infolist():
        if info.is_dir() or info.filename == 'SIGNATURE':
            continue
        records.append([info.filename, hashlib.sha256(z.read(info)).hexdigest()])
    return json.dumps(sorted(records), ensure_ascii=False, separators=(',', ':')).encode()


def verify_bundle(path: Path, desired_kind: str) -> tuple[dict, list]:
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError('Пакет слишком большой')
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        names = [i.filename for i in infos]
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise ValueError('Превышен лимит распакованного пакета')
        if len(names) != len(set(names)):
            raise ValueError('Дублирующиеся пути')
        for i in z.infolist():
            path_obj = Path(i.filename)
            if (i.filename.startswith('/') or '\\' in i.filename or
                '..' in path_obj.parts or path_obj.parts[0] == '.' or
                i.is_dir() and i.filename == '/' or
                (i.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Запрещённый путь или символическая ссылка')
        if 'manifest.json' not in names or 'SIGNATURE' not in names:
            raise ValueError('Нет manifest.json или SIGNATURE')
        manifest = json.loads(z.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('kind') != desired_kind:
            raise ValueError('Неверный тип пакета')
        if not VERSION_PATTERN.fullmatch(str(manifest.get('version', ''))):
            raise ValueError('Неверная версия пакета')
        if desired_kind == 'module':
            if not safe_id(str(manifest.get('id', ''))) or manifest.get('entry') != 'index.html':
                raise ValueError('Неверный идентификатор модуля или точка входа')
            if 'index.html' not in names:
                raise ValueError('Отсутствует index.html')
            if manifest.get('position', 'top-right') not in ('top-left', 'top-right', 'bottom-left', 'bottom-right'):
                raise ValueError('Недопустимое расположение виджета')
            if any('/' in name for name in names):
                raise ValueError('Для изоляции модуля разрешены только файлы в корне пакета')
        else:
            if manifest.get('id') != 'pi-aerial' or not {'app/main.py', 'web/display.html', 'VERSION'}.issubset(names):
                raise ValueError('Неполный пакет ядра')
            if any(name != 'VERSION' and not name.startswith(('app/', 'web/', 'tools/')) for name in names if name not in ('manifest.json','SIGNATURE')):
                raise ValueError('Неожиданный файл в ядре')
            if z.read('VERSION').decode().strip() != manifest['version']:
                raise ValueError('Версия в VERSION не совпадает')
        keys = list((DATA / 'trusted_keys').glob('*.pem'))
        if not keys:
            raise ValueError('Не настроены доверенные ключи обновления; см. README')
        signature = base64.b64decode(z.read('SIGNATURE'), validate=True)
        payload = signed_payload(z)
        verified = False
        for key in keys:
            try:
                public_key = serialization.load_pem_public_key(key.read_bytes())
                if not isinstance(public_key, Ed25519PublicKey):
                    continue
                public_key.verify(signature, payload)
                verified = True
                break
            except Exception:
                # A malformed or non-matching trusted key is not authorization.
                continue
        if not verified:
            raise ValueError('Цифровая подпись не совпала ни с одним доверенным ключом')
        return manifest, infos


def extract_verified(path: Path, destination: Path, kind: str) -> dict:
    manifest, infos = verify_bundle(path, kind)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(path) as z:
            for info in infos:
                if info.filename in ('SIGNATURE', 'manifest.json'):
                    continue
                target = destination / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return manifest


def list_modules() -> list[dict]:
    modules = []
    root = DATA / 'modules'
    for entry in sorted(root.iterdir()) if root.exists() else []:
        if not entry.is_dir() or not safe_id(entry.name):
            continue
        metadata = read_json(entry / 'current' / 'manifest.json')
        if metadata.get('kind') == 'module' and metadata.get('id') == entry.name:
            modules.append({k: metadata.get(k) for k in ('id','name','version','position','description')})
    return modules


def install_module(bundle: Path) -> dict:
    with BUNDLE_LOCK:
        manifest, _ = verify_bundle(bundle, 'module')
        module_id = manifest['id']
        root = DATA / 'modules' / module_id
        root.mkdir(parents=True, exist_ok=True)
        stage = root / ('staging-' + os.urandom(6).hex())
        extract_verified(bundle, stage, 'module')
        current, previous = root / 'current', root / 'previous'
        if previous.exists():
            shutil.rmtree(previous)
        if current.exists():
            os.replace(current, previous)
        os.replace(stage, current)
        return manifest



def validate_source_module(source: Path) -> dict:
    """Validate a module checked out from the pinned GitHub repository."""
    if not source.is_dir() or source.is_symlink():
        raise ValueError('Каталог модуля не найден')
    files = list(source.iterdir())
    if not files or len(files) > MAX_FILES:
        raise ValueError('Недопустимое число файлов модуля')
    total = 0
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Модуль может содержать только обычные файлы')
        total += path.stat().st_size
    if total > MAX_UNPACKED_BYTES:
        raise ValueError('Модуль превышает допустимый размер')
    manifest = read_json(source / 'manifest.json')
    if manifest.get('kind') != 'module' or not safe_id(str(manifest.get('id', ''))):
        raise ValueError('Недопустимый manifest.json')
    if not VERSION_PATTERN.fullmatch(str(manifest.get('version', ''))):
        raise ValueError('Недопустимая версия модуля')
    if manifest.get('entry') != 'index.html' or not (source / 'index.html').is_file():
        raise ValueError('Модуль должен содержать index.html')
    if manifest.get('position', 'top-right') not in ('top-left', 'top-right', 'bottom-left', 'bottom-right'):
        raise ValueError('Недопустимое расположение модуля')
    return manifest


def install_module_directory(source: Path) -> dict:
    """Install a validated module from the SSH-authenticated GitHub checkout."""
    with BUNDLE_LOCK:
        manifest = validate_source_module(source)
        root = DATA / 'modules' / manifest['id']
        root.mkdir(parents=True, exist_ok=True)
        stage = root / ('staging-' + os.urandom(6).hex())
        try:
            stage.mkdir()
            for file in source.iterdir():
                shutil.copy2(file, stage / file.name, follow_symlinks=False)
            current, previous = root / 'current', root / 'previous'
            if previous.exists():
                shutil.rmtree(previous)
            if current.exists():
                os.replace(current, previous)
            os.replace(stage, current)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return manifest

def rollback_module(module_id: str) -> dict:
    if not safe_id(module_id):
        raise ValueError('Неверный идентификатор модуля')
    with BUNDLE_LOCK:
        root = DATA / 'modules' / module_id
        current, previous = root / 'current', root / 'previous'
        if not previous.is_dir():
            raise ValueError('Предыдущая версия недоступна')
        old = root / ('rollback-' + os.urandom(6).hex())
        if current.exists():
            os.replace(current, old)
        os.replace(previous, current)
        if old.exists():
            os.replace(old, previous)
        return read_json(current / 'manifest.json')


def stage_core(bundle: Path) -> dict:
    with BUNDLE_LOCK, LOCK:
        if update_control.paused():
            raise ValueError('Предыдущее обновление ещё выполняется')
        manifest, _ = verify_bundle(bundle, 'core')
        version = manifest['version']
        releases = BASE / 'releases'
        releases.mkdir(parents=True, exist_ok=True)
        dest = releases / version
        if dest.exists():
            raise ValueError('Эта версия уже существует')
        extract_verified(bundle, dest, 'core')
        # This marker is consumed by a separate user-level systemd update helper.
        update_control.request({'version': version})
        return manifest
