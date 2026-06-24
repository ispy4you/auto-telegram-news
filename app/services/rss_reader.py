import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionLog, MediaItem, MediaType, RawPost, RawPostStatus, SourceChannel
from app.services.media_storage import MediaStorageService
from app.services.text_cleanup import clean_telegram_rss_text, is_telegram_rss_garbage


class RssReaderService:
    def __init__(self):
        self.settings = get_settings()
        self.media_storage = MediaStorageService()

    @staticmethod
    def _entry_message_id(entry) -> int:
        value = entry.get("id") or entry.get("link") or entry.get("title") or str(datetime.utcnow().timestamp())
        return int(hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12], 16)

    @staticmethod
    def _entry_text(entry) -> str:
        text = entry.get("summary") or entry.get("description") or entry.get("title") or ""
        return clean_telegram_rss_text(text)

    def _media_urls(self, entry) -> list[str]:
        urls: list[str] = []
        for enc in getattr(entry, "enclosures", []) or []:
            href = enc.get("href")
            if href:
                urls.append(href)

        for media in entry.get("media_content", []) or []:
            url = media.get("url")
            if url:
                urls.append(url)

        thumb = entry.get("media_thumbnail")
        if isinstance(thumb, list):
            for item in thumb:
                url = item.get("url")
                if url:
                    urls.append(url)
        elif isinstance(thumb, dict):
            url = thumb.get("url")
            if url:
                urls.append(url)

        deduped: list[str] = []
        for url in urls:
            if url not in deduped:
                deduped.append(url)
        return deduped

    def _guess_media_type(self, url: str, content_type: str | None = None) -> str:
        if content_type:
            if content_type.startswith("video"):
                return MediaType.VIDEO.value
            if content_type.startswith("image"):
                return MediaType.PHOTO.value
        path = urlparse(url).path.lower()
        if path.endswith((".mp4", ".mov", ".webm")):
            return MediaType.VIDEO.value
        if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return MediaType.PHOTO.value
        return MediaType.UNKNOWN.value

    def _guess_extension(self, media_type: str, content_type: str | None = None) -> str:
        if content_type:
            if "jpeg" in content_type:
                return "jpg"
            if "png" in content_type:
                return "png"
            if "webp" in content_type:
                return "webp"
            if "mp4" in content_type:
                return "mp4"
        if media_type == MediaType.VIDEO.value:
            return "mp4"
        if media_type == MediaType.PHOTO.value:
            return "jpg"
        return "bin"

    async def _download_media(self, db: Session, post: RawPost, url: str, sort_order: int) -> MediaItem | None:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type")
                file_size = len(resp.content)
                if not self.media_storage.validate_size(file_size):
                    db.add(ActionLog(action="media_skip", entity_type="RawPost", entity_id=str(post.id), message="RSS media too large"))
                    return None

                media_type = self._guess_media_type(url, content_type)
                ext = self._guess_extension(media_type, content_type)
                target_dir = self.media_storage.build_dir(post.source_id, post.id)
                path = target_dir / f"rss_{sort_order}.{ext}"
                path.write_bytes(resp.content)

                item = MediaItem(
                    raw_post_id=post.id,
                    telegram_message_id=post.telegram_message_id,
                    media_type=media_type,
                    file_path=str(path),
                    file_size=file_size,
                    mime_type=content_type,
                    sort_order=sort_order,
                )
                db.add(item)
                return item
        except Exception as exc:
            db.add(ActionLog(action="media_skip", entity_type="RawPost", entity_id=str(post.id), message=f"RSS media download failed: {exc}"))
            return None

    async def fetch_source(self, db: Session, source: SourceChannel, limit: int = 50) -> int:
        if not source.enabled or source.source_type != "rss" or not source.rss_url:
            return 0

        feed = feedparser.parse(source.rss_url)
        entries = feed.entries[:limit]
        new_count = 0

        for entry in reversed(entries):
            message_id = self._entry_message_id(entry)
            exists = db.scalar(
                select(RawPost).where(
                    RawPost.source_id == source.id,
                    RawPost.telegram_message_id == message_id,
                )
            )
            if exists:
                continue

            original_text = self._entry_text(entry)
            media_urls = self._media_urls(entry)

            if is_telegram_rss_garbage(original_text) and not media_urls:
                db.add(
                    ActionLog(
                        action="fetch_skip_garbage",
                        entity_type="SourceChannel",
                        entity_id=str(source.id),
                        message=f"Skipped RSS garbage entry: {entry.get('link') or entry.get('title')}",
                    )
                )
                continue

            post = RawPost(
                source_id=source.id,
                telegram_message_id=message_id,
                telegram_grouped_id=None,
                source_url=entry.get("link"),
                original_text=original_text,
                normalized_text="",
                text_hash="",
                published_at_source=(
                    datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else None
                ),
                has_media=bool(media_urls),
                media_count=0,
                status=RawPostStatus.NEW.value,
            )
            if is_telegram_rss_garbage(original_text) and media_urls:
                post.ai_suitable = False
                post.ai_skip_reason = "Только медиа без текста (RSS не передал caption)"

            db.add(post)
            db.flush()

            for idx, url in enumerate(media_urls[:10]):
                item = await self._download_media(db, post, url, idx)
                if item:
                    post.media_count += 1
                    post.has_media = True

            db.add(
                ActionLog(
                    action="fetch_post_rss",
                    entity_type="RawPost",
                    entity_id=str(post.id),
                    message=f"Fetched from RSS source {source.title}",
                )
            )
            new_count += 1

        source.last_fetched_at = datetime.utcnow()
        db.commit()
        return new_count
