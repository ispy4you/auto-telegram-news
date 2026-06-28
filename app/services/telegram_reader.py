import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from app.config import get_settings
from app.models import ActionLog, MediaItem, MediaType, RawPost, SourceChannel
from app.services.media_storage import MediaStorageService

# Глобальный лок — только один Telethon-клиент работает в любой момент времени,
# чтобы не было конкурентного доступа к SQLite-файлу сессии.
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

        Path(self.settings.telegram_session_path).parent.mkdir(parents=True, exist_ok=True)
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
            self.settings.telegram_session_path,
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

    async def fetch_source(self, db: Session, source: SourceChannel, limit: int = 50) -> int:
        if not source.enabled or source.source_type != "telethon":
            return 0
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
                "или сервер недоступен. Проверьте сеть и пересоздайте сессию: "
                "python -m app.cli.init_telegram_session"
            )

    @staticmethod
    def _fix_session_journal(session_path: str):
        """Удаляет застрявший journal-файл Telethon-сессии если он есть."""
        journal = Path(session_path + "-journal")
        if journal.exists():
            journal.unlink()

    async def _do_fetch(self, db: Session, source: SourceChannel, limit: int) -> int:
        self._fix_session_journal(self.settings.telegram_session_path)
        client = self._client()
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Сессия Telethon не авторизована или устарела. "
                    "Пересоздайте её: python -m app.cli.init_telegram_session"
                )

            me = await client.get_me()
            if getattr(me, "bot", False):
                raise RuntimeError(
                    "Telethon session — это бот-токен. Пересоздайте user session: "
                    "python -m app.cli.init_telegram_session"
                )

            pending, last_msg_id = await self._collect_pending(client, db, source, limit)
        finally:
            await client.disconnect()

        return self._flush_pending(db, source, pending, last_msg_id)

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

        for msg in standalone_messages:
            if db.scalar(select(RawPost).where(RawPost.source_id == source.id, RawPost.telegram_message_id == msg.id)):
                continue
            media_files = await self._download_media_list(source, msg.id, [msg], client)
            pending.append({"msg": msg, "album": None, "media_files": media_files})

        for grouped_id in albums:
            grouped_messages = sorted(albums[grouped_id], key=lambda m: m.id)
            if db.scalar(select(RawPost).where(RawPost.source_id == source.id, RawPost.telegram_grouped_id == grouped_id)):
                continue
            media_files = await self._download_media_list(source, grouped_messages[0].id, grouped_messages, client)
            pending.append({"msg": grouped_messages[0], "album": grouped_messages, "media_files": media_files})

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
        db.commit()
        return new_count

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
        post = RawPost(
            source_id=source.id,
            telegram_message_id=msg.id,
            telegram_grouped_id=msg.grouped_id,
            source_url=f"https://t.me/{source.username}/{msg.id}" if source.username else None,
            original_text=(msg.message or "").strip(),
            normalized_text="",
            text_hash="",
            published_at_source=msg.date,
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

        db.add(ActionLog(action="fetch_post_telethon", entity_type="RawPost", entity_id=str(post.id), message=f"Fetched from @{source.username}"))
        return post

