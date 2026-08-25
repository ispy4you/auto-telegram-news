import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from app.config import get_settings
from app.models import ActionLog, MediaItem, MediaType, RawPost, SourceChannel
from app.services import settings_registry, telegram_session_store
from app.services.media_storage import MediaStorageService

logger = logging.getLogger(__name__)

# Глобальный лок — только один Telethon-клиент работает в любой момент времени,
# чтобы фоновый слушатель, опрос и вход в админке не толкались за одну сессию.
_TELETHON_LOCK = asyncio.Lock()


class TelegramReaderService:
    def __init__(self):
        self.settings = get_settings()
        self.media_storage = MediaStorageService()

    def _build_proxy(self):
        """Returns proxy tuple for Telethon, or None if not configured."""
        ptype = self.settings.telegram_proxy_type.lower().strip()
        if not ptype or not self.settings.telegram_proxy_host:
            return None, None

        host = self.settings.telegram_proxy_host
        port = self.settings.telegram_proxy_port

        if ptype == "mtproxy":
            from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
            secret = self.settings.telegram_proxy_secret
            return (host, port, secret), ConnectionTcpMTProxyRandomizedIntermediate

        import socks
        socks_type = socks.SOCKS5 if ptype == "socks5" else socks.HTTP
        if self.settings.telegram_proxy_username:
            return (socks_type, host, port, True,
                    self.settings.telegram_proxy_username,
                    self.settings.telegram_proxy_password), None
        return (socks_type, host, port), None

    def _client(self) -> TelegramClient:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID и TELEGRAM_API_HASH не заданы в .env")

        proxy, connection = self._build_proxy()
        kwargs = dict(
            connection_retries=0,
            retry_delay=0,
            timeout=10,
        )
        if proxy:
            kwargs["proxy"] = proxy
        if connection:
            kwargs["connection"] = connection
        return TelegramClient(
            telegram_session_store.load_session(),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
            **kwargs,
        )

    @staticmethod
    def _extract_username(username_or_url: str) -> str:
        cleaned = username_or_url.strip()
        if "t.me/" in cleaned:
            cleaned = cleaned.split("t.me/")[-1]
            if cleaned.startswith("s/"):
                cleaned = cleaned[2:]
            cleaned = cleaned.split("/")[0]
        return cleaned.replace("@", "")

    async def fetch_source(self, db: Session, source: SourceChannel, limit: int | None = None) -> int:
        if not source.enabled or source.source_type != "telethon":
            return 0
        if limit is None:
            limit = settings_registry.get("default_lookback_limit", db)
        if not source.username:
            raise ValueError(f"Source {source.id} не имеет username для Telethon")

        try:
            async with _TELETHON_LOCK:
                return await asyncio.wait_for(
                    self._do_fetch(db, source, limit),
                    timeout=90,
                )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Timeout при подключении к Telegram для @{source.username}. "
                "Возможные причины: нет доступа к Telegram, сессия устарела, "
                "или сервер недоступен. Проверьте сеть и войдите в Telegram "
                "заново в админке."
            )

    async def _do_fetch(self, db: Session, source: SourceChannel, limit: int) -> int:
        client = self._client()
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Сессия Telethon не авторизована или устарела. "
                    "Войдите в Telegram заново в админке."
                )

            me = await client.get_me()
            if getattr(me, "bot", False):
                raise RuntimeError(
                    "Telethon session — это бот-токен. Нужен вход "
                    "пользовательским аккаунтом — войдите заново в админке."
                )

            pending, last_msg_id = await self._collect_pending(client, db, source, limit)
        finally:
            await client.disconnect()

        return self._flush_pending(db, source, pending, last_msg_id)

    @staticmethod
    def _max_post_age_cutoff(db: Session) -> datetime | None:
        """Посты старше этого момента игнорируются при сборе (не тянем недельный
        бэклог после долгого простоя). 0 в настройках — отключить отсечку."""
        max_age_hours = settings_registry.get("max_post_age_hours", db)
        if max_age_hours <= 0:
            return None
        return datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    async def _collect_pending(
        self, client: TelegramClient, db: Session, source: SourceChannel, limit: int
    ) -> tuple[list[dict], int | None]:
        """Фаза 1: собирает сообщения и скачивает медиа. Клиент должен быть подключён."""
        albums: dict[int, list] = defaultdict(list)
        standalone_messages = []

        entity = await client.get_entity(source.username)
        if source.last_message_id:
            fetch_kwargs: dict = {"limit": limit, "min_id": source.last_message_id}
        else:
            fetch_kwargs = {"limit": 5}

        messages = [m async for m in client.iter_messages(entity, **fetch_kwargs)]
        messages = list(reversed(messages))

        for msg in messages:
            if not msg:
                continue
            if msg.grouped_id:
                albums[msg.grouped_id].append(msg)
            else:
                standalone_messages.append(msg)

        pending: list[dict] = []
        cutoff = self._max_post_age_cutoff(db)
        skipped_old = 0

        for msg in standalone_messages:
            if cutoff and msg.date and msg.date < cutoff:
                skipped_old += 1
                continue
            if db.scalar(select(RawPost).where(RawPost.source_id == source.id, RawPost.telegram_message_id == msg.id)):
                continue
            media_files = await self._download_media_list(source, msg.id, [msg], client)
            pending.append({"msg": msg, "album": None, "media_files": media_files})

        for grouped_id in albums:
            grouped_messages = sorted(albums[grouped_id], key=lambda m: m.id)
            anchor = grouped_messages[0]
            if cutoff and anchor.date and anchor.date < cutoff:
                skipped_old += 1
                continue
            if db.scalar(select(RawPost).where(RawPost.source_id == source.id, RawPost.telegram_grouped_id == grouped_id)):
                continue
            media_files = await self._download_media_list(source, grouped_messages[0].id, grouped_messages, client)
            pending.append({"msg": grouped_messages[0], "album": grouped_messages, "media_files": media_files})

        if skipped_old:
            logger.debug("Пропущено %d старых постов (старше отсечки) у @%s", skipped_old, source.username)

        last_msg_id = max((m.id for m in messages), default=None)
        return pending, last_msg_id

    def _flush_pending(
        self, db: Session, source: SourceChannel, pending: list[dict], last_msg_id: int | None
    ) -> int:
        """Фаза 2: записывает накопленные посты в DB."""
        new_count = 0
        for item in pending:
            post = self._write_post(db, source, item["msg"], item["album"], item["media_files"])
            if post:
                new_count += 1

        source.last_fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if last_msg_id:
            source.last_message_id = last_msg_id
        if new_count:
            # Одна запись на весь батч, а не на каждый пост — иначе лог захлёбывается
            # после долгого простоя (сотни постов = сотни строк "Fetched from @...").
            db.add(ActionLog(
                action="fetch_post_telethon",
                entity_type="SourceChannel",
                entity_id=str(source.id),
                message=f"Собрано {new_count} новых постов из @{source.username}",
            ))
        db.commit()
        return new_count

    async def restore_media(self, db: Session, raw_post: RawPost) -> int:
        """Перекачивает медиа поста из исходного канала, если файлов нет на диске.

        Диск — кэш, а не хранилище: на хостинге он не переживает деплой. Источник
        правды по медиа — сам Telegram, сообщение по-прежнему лежит в канале.
        Возвращает число восстановленных файлов.
        """
        missing = [m for m in raw_post.media_items if not Path(m.file_path).exists()]
        if not missing:
            return 0
        source = raw_post.source
        if not source or not source.username:
            return 0

        # Второй Telethon-клиент на той же сессии проект не поднимает намеренно:
        # именно поэтому планировщик пропускает опрос, пока работает слушатель.
        # Если слушатель жив — работаем его клиентом, а не своим.
        from app.services import telegram_event_listener

        live = telegram_event_listener.active_client()
        if live is not None:
            return await asyncio.wait_for(
                self._do_restore_media(db, source, raw_post, missing, live),
                timeout=180,
            )

        async with _TELETHON_LOCK:
            return await asyncio.wait_for(
                self._do_restore_media(db, source, raw_post, missing, None),
                timeout=180,
            )

    async def _do_restore_media(self, db: Session, source: SourceChannel, raw_post: RawPost, missing: list, client) -> int:
        own_client = client is None
        if own_client:
            client = self._client()
            await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Сессия Telethon не авторизована или устарела. "
                    "Войдите в Telegram заново в админке."
                )
            entity = await client.get_entity(self._extract_username(source.username))
            ids = sorted({item.telegram_message_id for item in missing})
            messages = [m for m in await client.get_messages(entity, ids=ids) if m is not None]
            if not messages:
                return 0
            files = await self._download_media_list(source, raw_post.telegram_message_id, messages, client)
        finally:
            if own_client:
                await client.disconnect()

        by_message = {f["telegram_message_id"]: f for f in files}
        restored = 0
        for item in missing:
            downloaded = by_message.get(item.telegram_message_id)
            if not downloaded:
                continue
            item.file_path = downloaded["path"]
            item.file_size = downloaded["file_size"]
            restored += 1
        if restored:
            db.commit()
        return restored

    async def _download_media_list(self, source: SourceChannel, anchor_msg_id: int, msgs: list, client: TelegramClient) -> list[dict]:
        """Скачивает медиафайлы на диск. Не трогает DB."""
        result = []
        for i, msg in enumerate(msgs):
            if not msg.media:
                continue
            file_size = getattr(msg.file, "size", None)
            if not self.media_storage.validate_size(file_size):
                continue

            ext, media_type = "bin", MediaType.UNKNOWN.value
            if isinstance(msg.media, MessageMediaPhoto):
                ext, media_type = "jpg", MediaType.PHOTO.value
            elif isinstance(msg.media, MessageMediaDocument):
                mime = getattr(msg.file, "mime_type", "") or ""
                if mime.startswith("video"):
                    ext, media_type = "mp4", MediaType.VIDEO.value
                else:
                    ext, media_type = "dat", MediaType.DOCUMENT.value

            # Используем source_id и anchor_msg_id как ключ пути (post.id ещё неизвестен)
            target_dir = self.media_storage.build_dir(source.id, anchor_msg_id)
            path = target_dir / f"{msg.id}_{i}.{ext}"
            try:
                await client.download_media(msg, file=path)
            except Exception:
                continue

            result.append({
                "path": str(path),
                "media_type": media_type,
                "telegram_message_id": msg.id,
                "file_size": file_size,
                "mime_type": getattr(msg.file, "mime_type", None),
                "width": getattr(msg.file, "width", None),
                "height": getattr(msg.file, "height", None),
                "duration": getattr(msg.file, "duration", None),
                "sort_order": i,
            })
        return result

    def _write_post(self, db: Session, source: SourceChannel, msg, album_messages, media_files: list[dict]):
        """Записывает пост и медиа в DB. Никакого async I/O."""
        # Повторная проверка дубля: между collect_pending и здесь могла сработать
        # другая корутина (event listener / параллельный fetch) и вставить тот же пост.
        if db.scalar(select(RawPost).where(
            RawPost.source_id == source.id,
            RawPost.telegram_message_id == msg.id,
        )):
            return None
        post = RawPost(
            source_id=source.id,
            telegram_message_id=msg.id,
            telegram_grouped_id=msg.grouped_id,
            source_url=f"https://t.me/{source.username}/{msg.id}" if source.username else None,
            original_text=(msg.message or "").strip(),
            normalized_text="",
            text_hash="",
            # msg.date is tz-aware UTC; the column is TIMESTAMP WITHOUT TIME ZONE, and
            # psycopg2 silently converts aware datetimes to the session's TimeZone
            # (server locale, e.g. Europe/Moscow) before dropping tzinfo — so we must
            # strip it ourselves first, or the stored value ends up shifted by the
            # server's UTC offset instead of being naive UTC like every other timestamp.
            published_at_source=msg.date.astimezone(timezone.utc).replace(tzinfo=None) if msg.date else None,
            has_media=bool(media_files),
            media_count=len(media_files),
        )
        db.add(post)
        db.flush()  # получаем post.id — write-лок открывается здесь, но только на миллисекунды

        if not post.original_text and not media_files:
            db.delete(post)
            return None

        for mf in media_files:
            db.add(MediaItem(
                raw_post_id=post.id,
                telegram_message_id=mf["telegram_message_id"],
                media_type=mf["media_type"],
                file_path=mf["path"],
                file_size=mf["file_size"],
                mime_type=mf["mime_type"],
                width=mf["width"],
                height=mf["height"],
                duration=mf["duration"],
                sort_order=mf["sort_order"],
            ))

        return post

