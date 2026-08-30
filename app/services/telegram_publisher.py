import html
import logging
import zoneinfo
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import post_lifecycle, settings_registry
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, PublishJob, PublishJobStatus, TargetChannel

logger = logging.getLogger(__name__)


def _is_within_window(publish_from: str, publish_to: str, tz_name: str) -> bool:
    tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.now(tz).time().replace(second=0, microsecond=0)
    from_t = time.fromisoformat(publish_from)
    to_t = time.fromisoformat(publish_to)
    if from_t <= to_t:
        return from_t <= now <= to_t
    # overnight window: e.g. 22:00–06:00
    return now >= from_t or now <= to_t


def _format_for_telegram(text: str, as_blockquote: bool) -> tuple[str, str | None]:
    """Готовый к отправке текст и режим разбора.

    Разметку в посте мы не поддерживаем и не хотим: включив HTML, пришлось бы
    отвечать за каждый `<` и `&` в новости, иначе Telegram отвергает сообщение
    целиком. Поэтому цитата — единственный тег, а сам текст экранируется.
    """
    if not as_blockquote:
        return text, None
    return f"<blockquote>{html.escape(text, quote=False)}</blockquote>", "HTML"


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
        self._bot_token: str | None = None

    async def _get_bot(self, db: Session | None = None) -> Bot:
        token = settings_registry.get("telegram_bot_token", db)
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN не задан ни в Settings, ни в .env")
        if self._bot is not None and self._bot_token != token:
            await self._bot.session.close()
            self._bot = None
        if self._bot is None:
            self._bot = Bot(token)
            self._bot_token = token
        return self._bot

    async def close(self):
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None

    async def test_target(self, chat_id: str, db: Session | None = None) -> tuple[bool, str]:
        bot = await self._get_bot(db)
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
            ok = member.status in {"administrator", "creator"}
            return ok, str(member.status)
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _find_job(db: Session, generated_post_id: int, target_channel_id: int) -> PublishJob | None:
        return db.scalar(
            select(PublishJob).where(
                PublishJob.generated_post_id == generated_post_id,
                PublishJob.target_channel_id == target_channel_id,
            )
        )

    @staticmethod
    def _claim_job(db: Session, generated_post_id: int, target_channel_id: int, status: str) -> PublishJob:
        """Одна задача на пару (пост, канал).

        Раньше каждая попытка публикации создавала новую задачу со счётчиком 1,
        а ретрай удалял старую — из-за чего ограничение на число попыток никогда
        не срабатывало. Задачу переиспользуем, чтобы attempts реально рос.
        """
        job = TelegramPublisherService._find_job(db, generated_post_id, target_channel_id)
        if job is None:
            job = PublishJob(
                generated_post_id=generated_post_id,
                target_channel_id=target_channel_id,
                status=status,
                attempts=0,
            )
            db.add(job)
        job.status = status
        return job

    @staticmethod
    async def _restore_missing_media(db: Session, raw_post) -> int:
        """Перекачка медиа не должна ронять публикацию: без картинок пост всё ещё нужен."""
        from app.services.telegram_reader import TelegramReaderService

        try:
            return await TelegramReaderService().restore_media(db, raw_post)
        except Exception as exc:
            logger.warning("Не удалось перекачать медиа для поста %s: %s", raw_post.id, exc)
            return 0

    async def publish_generated_post(self, db: Session, generated_post_id: int, target_channel_id: int, publish_text_only_on_missing_media: bool = True, include_media: bool = True):
        generated = db.get(GeneratedPost, generated_post_id)
        target = db.get(TargetChannel, target_channel_id)
        if not generated or not target:
            raise ValueError("Generated post or target not found")

        # Пост может уходить в несколько каналов, поэтому «уже опубликован»
        # проверяется по паре (пост, канал), а не по статусу поста целиком.
        done = self._find_job(db, generated_post_id, target_channel_id)
        if done is not None and done.status == PublishJobStatus.SUCCESS.value:
            db.add(ActionLog(
                action="publish_skipped",
                entity_type="GeneratedPost",
                entity_id=str(generated.id),
                message=f"Уже опубликован в {target.chat_id}",
            ))
            db.commit()
            return

        text = (generated.edited_text or generated.generated_text or "").strip()
        if not text:
            raise ValueError("Empty publish text")
        # Длину для подписи считаем по исходному тексту: Telegram меряет
        # разобранное сообщение, а не разметку вокруг него.
        body, parse_mode = _format_for_telegram(text, bool(generated.as_blockquote))

        raw_post = generated.raw_post
        if not raw_post:
            raise ValueError(f"RawPost not found for GeneratedPost {generated_post_id}")

        # Check publish window
        if target.publish_from and target.publish_to:
            tz_name = settings_registry.get("display_timezone", db)
            if not _is_within_window(target.publish_from, target.publish_to, tz_name):
                scheduled_at = _next_window_open_utc(target.publish_from, tz_name)
                job = self._claim_job(db, generated_post_id, target_channel_id, PublishJobStatus.PENDING.value)
                post_lifecycle.schedule(generated, job, target_channel_id, scheduled_at)
                db.add(ActionLog(
                    action="publish_scheduled",
                    entity_type="GeneratedPost",
                    entity_id=str(generated.id),
                    message=f"Отложено до {scheduled_at.strftime('%H:%M')} UTC → {target.chat_id}",
                ))
                db.commit()
                return

        bot = await self._get_bot(db)
        job = self._claim_job(db, generated_post_id, target_channel_id, PublishJobStatus.RUNNING.value)
        job.attempts += 1
        job.scheduled_at = None
        db.flush()

        try:
            media = sorted(raw_post.media_items, key=lambda m: m.sort_order)
            if include_media and any(not Path(m.file_path).exists() for m in media):
                # Диск не переживает деплой, а сообщение в исходном канале — переживает.
                await self._restore_missing_media(db, raw_post)

            existing_media = [m for m in media if Path(m.file_path).exists()] if include_media else []

            if include_media and media and not existing_media and not publish_text_only_on_missing_media:
                raise FileNotFoundError("All media files missing")

            if include_media and len(existing_media) < len(media):
                # Раньше пропавшее медиа просто отфильтровывалось и пост уходил
                # текстом, а в панели он по-прежнему выглядел как пост с картинками.
                lost = len(media) - len(existing_media)
                logger.warning(
                    "Пост %s публикуется без %s из %s медиафайлов: восстановить не удалось",
                    generated.id, lost, len(media),
                )
                db.add(ActionLog(
                    action="media_missing",
                    entity_type="GeneratedPost",
                    entity_id=str(generated.id),
                    message=f"Публикация без {lost} из {len(media)} медиафайлов: восстановить из источника не удалось",
                ))

            # Длинный текст не влезает в подпись: медиа и текст уходят двумя
            # сообщениями. Если первое доставлено, а второе упало, id сохранён
            # на задаче — ретрай дошлёт только текст и не продублирует медиа.
            sent_msg_id: int | None = job.sent_message_id
            tail_text_pending = False

            if sent_msg_id is not None:
                await bot.send_message(chat_id=target.chat_id, text=body, parse_mode=parse_mode)
            elif not existing_media:
                msg = await bot.send_message(chat_id=target.chat_id, text=body, parse_mode=parse_mode)
                sent_msg_id = msg.message_id
            elif len(existing_media) == 1:
                m = existing_media[0]
                file = FSInputFile(m.file_path)
                caption = body if len(text) <= 1024 else None
                if m.media_type == "video":
                    msg = await bot.send_video(chat_id=target.chat_id, video=file, caption=caption, parse_mode=parse_mode)
                else:
                    msg = await bot.send_photo(chat_id=target.chat_id, photo=file, caption=caption, parse_mode=parse_mode)
                sent_msg_id = msg.message_id
                tail_text_pending = caption is None
            else:
                use_caption = len(text) <= 1024
                group = []
                for idx, m in enumerate(existing_media):
                    file = FSInputFile(m.file_path)
                    caption = body if idx == 0 and use_caption else None
                    if m.media_type == "video":
                        group.append(InputMediaVideo(media=file, caption=caption, parse_mode=parse_mode))
                    else:
                        group.append(InputMediaPhoto(media=file, caption=caption, parse_mode=parse_mode))
                msgs = await bot.send_media_group(chat_id=target.chat_id, media=group)
                sent_msg_id = msgs[0].message_id if msgs else None
                tail_text_pending = not use_caption

            if tail_text_pending:
                job.sent_message_id = sent_msg_id
                db.commit()
                await bot.send_message(chat_id=target.chat_id, text=body, parse_mode=parse_mode)

            post_lifecycle.mark_published(generated, raw_post, job, target_channel_id, sent_msg_id)
            db.add(ActionLog(action="publish_success", entity_type="GeneratedPost", entity_id=str(generated.id), message=f"Published to {target.chat_id}"))
            db.commit()
        except Exception as exc:
            post_lifecycle.mark_publish_failed(generated, raw_post, job, str(exc))
            db.add(ActionLog(action="publish_failed", entity_type="GeneratedPost", entity_id=str(generated.id), message=str(exc)))
            db.commit()
            raise
