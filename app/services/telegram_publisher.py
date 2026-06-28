import zoneinfo
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, PublishJob, PublishJobStatus, RawPostStatus, TargetChannel


def _is_within_window(publish_from: str, publish_to: str, tz_name: str) -> bool:
    tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.now(tz).time().replace(second=0, microsecond=0)
    from_t = time.fromisoformat(publish_from)
    to_t = time.fromisoformat(publish_to)
    if from_t <= to_t:
        return from_t <= now <= to_t
    # overnight window: e.g. 22:00–06:00
    return now >= from_t or now <= to_t


def _next_window_open_utc(publish_from: str, tz_name: str) -> datetime:
    tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.now(tz)
    from_t = time.fromisoformat(publish_from)
    next_open = now.replace(hour=from_t.hour, minute=from_t.minute, second=0, microsecond=0)
    if next_open <= now:
        next_open += timedelta(days=1)
    return next_open.astimezone(timezone.utc).replace(tzinfo=None)


class TelegramPublisherService:
    def __init__(self):
        self.settings = get_settings()
        self._bot: Bot | None = None

    def _get_bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(self.settings.telegram_bot_token)
        return self._bot

    async def close(self):
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None

    async def test_target(self, chat_id: str) -> tuple[bool, str]:
        bot = self._get_bot()
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
            ok = member.status in {"administrator", "creator"}
            return ok, str(member.status)
        except Exception as exc:
            return False, str(exc)

    async def publish_generated_post(self, db: Session, generated_post_id: int, target_channel_id: int, publish_text_only_on_missing_media: bool = True, include_media: bool = True):
        generated = db.get(GeneratedPost, generated_post_id)
        target = db.get(TargetChannel, target_channel_id)
        if not generated or not target:
            raise ValueError("Generated post or target not found")

        text = (generated.edited_text or generated.generated_text or "").strip()
        if not text:
            raise ValueError("Empty publish text")

        raw_post = generated.raw_post
        if not raw_post:
            raise ValueError(f"RawPost not found for GeneratedPost {generated_post_id}")

        # Check publish window
        if target.publish_from and target.publish_to:
            from app.services.prompt_settings import _get_setting
            tz_name = _get_setting(db, "display_timezone", "Europe/Moscow")
            if not _is_within_window(target.publish_from, target.publish_to, tz_name):
                scheduled_at = _next_window_open_utc(target.publish_from, tz_name)
                job = PublishJob(
                    generated_post_id=generated_post_id,
                    target_channel_id=target_channel_id,
                    status=PublishJobStatus.PENDING.value,
                    scheduled_at=scheduled_at,
                )
                db.add(job)
                generated.status = GeneratedPostStatus.SCHEDULED.value
                db.add(ActionLog(
                    action="publish_scheduled",
                    entity_type="GeneratedPost",
                    entity_id=str(generated.id),
                    message=f"Отложено до {scheduled_at.strftime('%H:%M')} UTC → {target.chat_id}",
                ))
                db.commit()
                return

        bot = self._get_bot()
        job = PublishJob(generated_post_id=generated_post_id, target_channel_id=target_channel_id, status=PublishJobStatus.RUNNING.value, attempts=1)
        db.add(job)
        db.flush()

        try:
            media = sorted(raw_post.media_items, key=lambda m: m.sort_order)
            existing_media = [m for m in media if Path(m.file_path).exists()] if include_media else []

            if include_media and media and not existing_media and not publish_text_only_on_missing_media:
                raise FileNotFoundError("All media files missing")

            if not existing_media:
                await bot.send_message(chat_id=target.chat_id, text=text)
            elif len(existing_media) == 1:
                m = existing_media[0]
                file = FSInputFile(m.file_path)
                caption = text if len(text) <= 1024 else None
                if m.media_type == "video":
                    await bot.send_video(chat_id=target.chat_id, video=file, caption=caption)
                else:
                    await bot.send_photo(chat_id=target.chat_id, photo=file, caption=caption)
                if caption is None:
                    await bot.send_message(chat_id=target.chat_id, text=text)
            else:
                use_caption = len(text) <= 1024
                group = []
                for idx, m in enumerate(existing_media):
                    file = FSInputFile(m.file_path)
                    caption = text if idx == 0 and use_caption else None
                    if m.media_type == "video":
                        group.append(InputMediaVideo(media=file, caption=caption))
                    else:
                        group.append(InputMediaPhoto(media=file, caption=caption))
                await bot.send_media_group(chat_id=target.chat_id, media=group)
                if not use_caption:
                    await bot.send_message(chat_id=target.chat_id, text=text)

            generated.status = GeneratedPostStatus.PUBLISHED.value
            generated.published_at = datetime.now(timezone.utc).replace(tzinfo=None)
            raw_post.status = RawPostStatus.PUBLISHED.value
            job.status = PublishJobStatus.SUCCESS.value
            db.add(ActionLog(action="publish_success", entity_type="GeneratedPost", entity_id=str(generated.id), message=f"Published to {target.chat_id}"))
            db.commit()
        except Exception as exc:
            generated.status = GeneratedPostStatus.FAILED.value
            generated.publish_error = str(exc)
            raw_post.status = RawPostStatus.FAILED.value
            job.status = PublishJobStatus.FAILED.value
            job.last_error = str(exc)
            db.add(ActionLog(action="publish_failed", entity_type="GeneratedPost", entity_id=str(generated.id), message=str(exc)))
            db.commit()
            raise
