#!/usr/bin/env python3
"""Set a local administration password without sending it over the network."""
import getpass
from app.storage import init_data
from app.auth import set_password

init_data()
first = getpass.getpass('Новый пароль веб-панели (минимум 12 символов): ')
second = getpass.getpass('Повторите пароль: ')
if first != second:
    raise SystemExit('Пароли не совпадают')
set_password(first)
print('Пароль сохранён')
