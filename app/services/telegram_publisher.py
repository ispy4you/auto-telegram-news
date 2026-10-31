import logging
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from aiogram.utils.token import TokenValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import media_restore, message_entities, post_lifecycle, settings_registry
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, PublishJob, PublishJobStatus, TargetChannel

logger = logging.getLogger(__name__)


class BotTokenError(RuntimeError):
    """Токен бота не задан или не проходит проверку формата.

    Отдельный тип, потому что это единственная ошибка публикации, которую
    оператор может исправить сам, не читая лог: текст пишется для него и
    показывается в панели как есть.
    """



#: Что прячет канал: настройка канала → типы медиа, которые уходят размытыми.
SPOILER_MODES = {
    "off": "Без спойлера",
    "photo": "Только фото",
    "video": "Только видео",
    "all": "Фото и видео",
}


def _media_kind(item) -> str:
    """«photo» или «video» — так их различает настройка спойлера."""
    return "video" if item.media_type == "video" else "photo"


@dataclass(frozen=True)
class _Payload:
    """Текст поста в том виде, в каком он уйдёт в Telegram.

    Собирается один раз до первой отправки: при длинном тексте медиа и текст
    уходят двумя сообщениями, и оба раза нужен один и тот же текст с одной и
    той же разметкой.
    """

    text: str
    entities: list | None
    #: Текст влезает в подпись к медиа. Не влезает — уйдёт отдельным сообщением.
    caption_fits: bool


def _spoiler_kinds(mode: str | None) -> frozenset[str]:
    """Типы медиа, которые этот канал прячет.

    Неизвестное значение читаем как «ничего не прятать»: показать лишнее
    безопаснее, чем спрятать то, что прятать не просили.
    """
    if mode == "all":
        return frozenset({"photo", "video"})
    if mode in ("photo", "video"):
        return frozenset({mode})
    return frozenset()


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
        self._bot_token: str | None = None

    async def _get_bot(self, db: Session | None = None) -> Bot:
        token = settings_registry.get("telegram_bot_token", db)
        if not token:
            raise BotTokenError(
                "Токен бота не задан. Настройки → Telegram-бот, либо TELEGRAM_BOT_TOKEN в окружении."
            )
        if self._bot is not None and self._bot_token != token:
            await self._bot.session.close()
            self._bot = None
        if self._bot is None:
            try:
                self._bot = Bot(token)
            except TokenValidationError:
                # aiogram проверяет формат, не спрашивая Telegram: «123456:буквы».
                # Без этой ветки панель отдавала пятисотку без единого слова,
                # а причина была в лишнем пробеле или обрезанном токене.
                raise BotTokenError(
                    "Токен бота не похож на настоящий: Telegram такой формат не принимает. "
                    "Скопируйте его из @BotFather целиком, без пробелов."
                ) from None
            self._bot_token = token
        return self._bot

    async def close(self):
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None

    async def test_target(self, chat_id: str, db: Session | None = None) -> tuple[bool, str]:
        try:
            # Получение бота тоже внутри try: при неверном токене проверка
            # должна отвечать «вот что не так», а не падать пятисоткой.
            bot = await self._get_bot(db)
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

    @staticmethod
    def _payload(generated: GeneratedPost) -> _Payload:
        """Что уходит в канал: текст, его разметка и влезает ли он в подпись."""
        text = (generated.edited_text or generated.generated_text or "").strip()
        if not text:
            raise ValueError("Empty publish text")
        # Разметка всегда описывает тот текст, который уходит: правил редактор —
        # берём его разметку, не правил — ту, что разобрана из ответа модели.
        # Перепутать нельзя: смещения указывали бы мимо.
        stored = message_entities.loads(
            generated.entities if generated.edited_text else generated.generated_entities
        )
        return _Payload(
            text=text,
            entities=message_entities.to_aiogram(stored) or None,
            # Лимит подписи Telegram тоже считает в единицах UTF-16.
            caption_fits=message_entities.utf16_len(text) <= 1024,
        )

    def _already_published(self, db: Session, generated: GeneratedPost, target: TargetChannel) -> bool:
        """Этот пост уже дошёл до этого канала?

        Пост может уходить в несколько каналов, поэтому проверяется пара
        (пост, канал), а не статус поста целиком.
        """
        done = self._find_job(db, generated.id, target.id)
        if done is None or done.status != PublishJobStatus.SUCCESS.value:
            return False
        db.add(ActionLog(
            action="publish_skipped",
            entity_type="GeneratedPost",
            entity_id=str(generated.id),
            message=f"Уже опубликован в {target.chat_id}",
        ))
        db.commit()
        return True

    def _defer_outside_window(self, db: Session, generated: GeneratedPost, target: TargetChannel) -> bool:
        """Отложить публикацию, если канал сейчас не принимает.

        True — отложено и больше сегодня ничего делать не нужно.
        """
        if not (target.publish_from and target.publish_to):
            return False
        tz_name = settings_registry.get("display_timezone", db)
        if _is_within_window(target.publish_from, target.publish_to, tz_name):
            return False

        scheduled_at = _next_window_open_utc(target.publish_from, tz_name)
        job = self._claim_job(db, generated.id, target.id, PublishJobStatus.PENDING.value)
        post_lifecycle.schedule(generated, job, target.id, scheduled_at)
        db.add(ActionLog(
            action="publish_scheduled",
            entity_type="GeneratedPost",
            entity_id=str(generated.id),
            message=f"Отложено до {scheduled_at.strftime('%H:%M')} UTC → {target.chat_id}",
        ))
        db.commit()
        return True

    async def _media_to_send(
        self,
        db: Session,
        generated: GeneratedPost,
        raw_post,
        include_media: bool,
        publish_text_only_on_missing_media: bool,
    ) -> list:
        """Файлы, которые реально дойдут до Telegram, по порядку.

        Пропавшее медиа сначала пробуем перекачать, и только потом решаем,
        уходит пост без картинок или не уходит вовсе.
        """
        if not include_media:
            return []

        media = sorted(raw_post.media_items, key=lambda m: m.sort_order)
        if media_restore.missing_media(raw_post):
            # Диск не переживает деплой, а сообщение в исходном канале —
            # переживает. Файл, загруженный редактором, в этот список не
            # попадает: перекачивать его неоткуда.
            await self._restore_missing_media(db, raw_post)

        existing = [m for m in media if Path(m.file_path).exists()]
        if media and not existing and not publish_text_only_on_missing_media:
            raise FileNotFoundError("All media files missing")

        if len(existing) < len(media):
            # Раньше пропавшее медиа просто отфильтровывалось и пост уходил
            # текстом, а в панели он по-прежнему выглядел как пост с картинками.
            lost = len(media) - len(existing)
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
        return existing

    async def _send(
        self,
        bot: Bot,
        target: TargetChannel,
        payload: _Payload,
        media: list,
        hidden: frozenset[str],
        job: PublishJob,
    ) -> tuple[int | None, bool]:
        """Отправляет пост и отвечает: id первого сообщения, остался ли текст.

        Текст остаётся, когда он не влез в подпись к медиа: тогда его дошлют
        вторым сообщением.
        """
        if job.sent_message_id is not None:
            # Медиа прошлая попытка уже доставила — дошлём только текст,
            # иначе ретрай продублировал бы картинки.
            await bot.send_message(chat_id=target.chat_id, text=payload.text, entities=payload.entities)
            return job.sent_message_id, False

        if not media:
            msg = await bot.send_message(chat_id=target.chat_id, text=payload.text, entities=payload.entities)
            return msg.message_id, False

        if len(media) == 1:
            item = media[0]
            kind = _media_kind(item)
            file = FSInputFile(item.file_path)
            caption = payload.text if payload.caption_fits else None
            caption_entities = payload.entities if payload.caption_fits else None
            if kind == "video":
                msg = await bot.send_video(
                    chat_id=target.chat_id, video=file, caption=caption,
                    caption_entities=caption_entities, has_spoiler=kind in hidden,
                )
            else:
                msg = await bot.send_photo(
                    chat_id=target.chat_id, photo=file, caption=caption,
                    caption_entities=caption_entities, has_spoiler=kind in hidden,
                )
            return msg.message_id, caption is None

        group = []
        for idx, item in enumerate(media):
            kind = _media_kind(item)
            first = idx == 0 and payload.caption_fits
            group.append((InputMediaVideo if kind == "video" else InputMediaPhoto)(
                media=FSInputFile(item.file_path),
                caption=payload.text if first else None,
                caption_entities=payload.entities if first else None,
                has_spoiler=kind in hidden,
            ))
        msgs = await bot.send_media_group(chat_id=target.chat_id, media=group)
        return (msgs[0].message_id if msgs else None), not payload.caption_fits

    async def publish_generated_post(self, db: Session, generated_post_id: int, target_channel_id: int, publish_text_only_on_missing_media: bool = True, include_media: bool = True):
        generated = db.get(GeneratedPost, generated_post_id)
        target = db.get(TargetChannel, target_channel_id)
        if not generated or not target:
            raise ValueError("Generated post or target not found")

        if self._already_published(db, generated, target):
            return

        payload = self._payload(generated)

        raw_post = generated.raw_post
        if not raw_post:
            raise ValueError(f"RawPost not found for GeneratedPost {generated_post_id}")

        if self._defer_outside_window(db, generated, target):
            return

        job = self._claim_job(db, generated_post_id, target_channel_id, PublishJobStatus.RUNNING.value)
        job.attempts += 1
        job.scheduled_at = None
        db.flush()

        try:
            # Всё внутри try, чтобы неверный токен попал в причину отказа задачи
            # и в журнал — раньше он улетал наружу мимо отметки publish_failed.
            bot = await self._get_bot(db)
            # Спойлер — свойство канала, и прячет он то, что выбрано: только
            # фото, только видео или всё. Размывает Telegram на своей стороне,
            # файл уходит нетронутым и в исходнике остаётся целым.
            hidden = _spoiler_kinds(target.spoiler_media_mode)
            media = await self._media_to_send(
                db, generated, raw_post, include_media, publish_text_only_on_missing_media
            )
            sent_msg_id, tail_text_pending = await self._send(bot, target, payload, media, hidden, job)

            if tail_text_pending:
                # Медиа доставлено, а текст ещё нет. Id сохраняем до отправки:
                # если второе сообщение упадёт, ретрай дошлёт только текст.
                job.sent_message_id = sent_msg_id
                db.commit()
                await bot.send_message(chat_id=target.chat_id, text=payload.text, entities=payload.entities)

            post_lifecycle.mark_published(generated, raw_post, job, target_channel_id, sent_msg_id)
            db.add(ActionLog(action="publish_success", entity_type="GeneratedPost", entity_id=str(generated.id), message=f"Published to {target.chat_id}"))
            db.commit()
        except Exception as exc:
            post_lifecycle.mark_publish_failed(generated, raw_post, job, str(exc))
            db.add(ActionLog(action="publish_failed", entity_type="GeneratedPost", entity_id=str(generated.id), message=str(exc)))
            db.commit()
            raise
