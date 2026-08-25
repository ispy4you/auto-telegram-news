"""Telethon-сессия хранится в БД (таблица app_settings), а не файлом на диске.

На платформах вроде Timeweb App Platform контейнер пересоздаётся при каждом
деплое, и файловая сессия означала бы повторный вход по QR после каждой выкатки.
Строка сессии (StringSession) содержит auth key аккаунта — то есть полный доступ
к нему, поэтому обращаться с ней надо как с паролем.
"""

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session
from telethon.sessions import StringSession

from app.database import SessionLocal
from app.models import AppSetting

logger = logging.getLogger(__name__)

SETTING_KEY = "telegram_session_string"
# Кто именно подключён: строку сессии не расшифровать, а показать аккаунт надо
# и после перезапуска, когда логина в этом процессе не было.
ACCOUNT_KEY = "telegram_account"


def load_string(db: Session | None = None) -> str | None:
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, SETTING_KEY)
        value = (row.value or "").strip() if row else ""
        return value or None
    finally:
        if own_session:
            session.close()


def save_string(value: str, db: Session | None = None) -> None:
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, SETTING_KEY)
        if row:
            row.value = value
        else:
            session.add(AppSetting(key=SETTING_KEY, value=value))
        session.commit()
    finally:
        if own_session:
            session.close()


def clear(db: Session | None = None) -> None:
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, SETTING_KEY)
        if row:
            session.delete(row)
            session.commit()
    finally:
        if own_session:
            session.close()


def load_session(db: Session | None = None) -> StringSession:
    """Сессия для TelegramClient. Пустая означает, что вход ещё не выполнен."""
    return StringSession(load_string(db) or "")


def save_from_client(client, db: Session | None = None) -> None:
    """Сохраняет сессию подключённого клиента. Вызывать после успешного входа."""
    value = StringSession.save(client.session)
    if not value:
        logger.warning("Telethon-сессия пуста — сохранять нечего")
        return
    save_string(value, db)


def migrate_legacy_file(path: str, db: Session | None = None) -> bool:
    """Переносит старую файловую сессию в БД. True — если перенос состоялся.

    Нужно ровно один раз, при обновлении установки, которая логинилась до
    переезда хранилища в БД. Файл после переноса не удаляется.
    """
    if load_string(db):
        return False
    if not path or not Path(path).exists():
        return False
    try:
        from telethon.sessions import SQLiteSession

        legacy = SQLiteSession(path)
        value = StringSession.save(legacy)
        legacy.close()
    except Exception as exc:
        logger.warning("Не удалось перенести файловую Telethon-сессию %s: %s", path, exc)
        return False
    if not value:
        return False
    save_string(value, db)
    logger.info("Telethon-сессия перенесена из %s в БД", path)
    return True


def load_account(db: Session | None = None) -> dict | None:
    """Данные подключённого аккаунта, сохранённые при входе."""
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, ACCOUNT_KEY)
        raw = (row.value or "").strip() if row else ""
    finally:
        if own_session:
            session.close()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def save_account(me, db: Session | None = None) -> None:
    """Строку сессии не расшифровать, поэтому кто подключён — храним отдельно."""
    payload = json.dumps({
        "id": getattr(me, "id", None),
        "first_name": getattr(me, "first_name", None) or "",
        "last_name": getattr(me, "last_name", None) or "",
        "username": getattr(me, "username", None) or "",
    })
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, ACCOUNT_KEY)
        if row:
            row.value = payload
        else:
            session.add(AppSetting(key=ACCOUNT_KEY, value=payload))
        session.commit()
    finally:
        if own_session:
            session.close()


def clear_account(db: Session | None = None) -> None:
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, ACCOUNT_KEY)
        if row:
            session.delete(row)
            session.commit()
    finally:
        if own_session:
            session.close()
