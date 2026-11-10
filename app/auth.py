"""Local network administrator login with scrypt, HttpOnly cookies and CSRF tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from fastapi import HTTPException, Request
from .storage import DATA, atomic_json, read_json

PASSWORD_FILE = DATA / 'admin_auth.json'
SECRET_FILE = DATA / 'auth_secret'
DURATION = 12 * 60 * 60


def set_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError('Пароль должен содержать минимум 12 символов')
    salt = os.urandom(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    atomic_json(PASSWORD_FILE, {'salt': salt.hex(), 'hash': value.hex()})


def check_password(password: str) -> bool:
    info = read_json(PASSWORD_FILE)
    if not info:
        return False
    try:
        expected = bytes.fromhex(info['hash'])
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(info['salt']), n=2**14, r=8, p=1)
        return hmac.compare_digest(value, expected)
    except (ValueError, KeyError):
        return False


def secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    value = secrets.token_bytes(32)
    try:
        fd = os.open(SECRET_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(value)
    except FileExistsError:
        return SECRET_FILE.read_bytes()
    return value


def token_for(username: str = 'admin') -> str:
    payload = f'{username}:{int(time.time()) + DURATION}:{secrets.token_hex(12)}'
    digest = hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f'{payload}:{digest}'.encode()).decode()


def verify_token(token: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        username, expires, nonce, digest = decoded.split(':')
        payload = f'{username}:{expires}:{nonce}'
        expected = hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()
        return username == 'admin' and int(expires) > time.time() and hmac.compare_digest(expected, digest)
    except (ValueError, UnicodeError, TypeError):
        return False


def csrf_for(token: str) -> str:
    return hmac.new(secret(), f'csrf:{token}'.encode(), hashlib.sha256).hexdigest()


def require_admin(request: Request) -> None:
    token = request.cookies.get('pa_session', '')
    if not verify_token(token):
        raise HTTPException(status_code=401, detail='Требуется вход в панель')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        incoming_origin = request.headers.get('origin')
        if incoming_origin and incoming_origin != f'{request.url.scheme}://{request.headers.get("host")}':
            raise HTTPException(status_code=403, detail='Неверный Origin')
        csrf = request.headers.get('x-csrf-token', '')
        if not hmac.compare_digest(csrf, csrf_for(token)):
            raise HTTPException(status_code=403, detail='Проверка CSRF не прошла')
