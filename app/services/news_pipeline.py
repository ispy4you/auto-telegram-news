import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel, SourceTargetRoute, TargetChannel
from app.services.ai_gateway import AiGatewayClient
from app.services.deduplication import DeduplicationService
from app.services.telegram_reader import TelegramReaderService
from app.services.telegram_publisher import TelegramPublisherService

logger = logging.getLogger(__name__)


class NewsPipelineService:
    def __init__(self):
        self.settings = get_settings()
        self.reader = TelegramReaderService()
        self.deduper = DeduplicationService()
        self.ai_client = AiGatewayClient()
        self.publisher = TelegramPublisherService()

    async def fetch_new_posts(self, db: Session):
        sources = db.scalars(select(SourceChannel).where(SourceChannel.enabled.is_(True))).all()
        for source in sources:
            try:
                await self.reader.fetch_source(db, source)
            except Exception as exc:
                msg = str(exc)
                if "Constructor ID" in msg and "TLObject" in msg:
                    msg = "Telethon: схема TL устарела (Telegram обновил API). Обновите библиотеку."
                elif len(msg) > 500:
                    msg = msg[:500] + "… [truncated]"
                try:
                    db.rollback()
                    db.add(ActionLog(action="fetch_error", entity_type="SourceChannel", entity_id=str(source.id), message=msg))
                    db.commit()
                except Exception:
                    logger.warning("Failed to record fetch_error ActionLog for source_id=%s", source.id, exc_info=True)

    async def process_ready_posts(self, db: Session):
        posts = db.scalars(select(RawPost).where(RawPost.status.in_([RawPostStatus.NEW.value, RawPostStatus.READY.value]))).all()
        for post in posts:
            self.deduper.deduplicate_post(db, post)
            if post.status != RawPostStatus.DUPLICATE.value:
                post.status = RawPostStatus.READY.value
        db.commit()

    def _resolve_targets(self, db: Session, raw_post: RawPost):
        routes = db.execute(
            select(SourceTargetRoute, TargetChannel)
            .join(TargetChannel, SourceTargetRoute.target_channel_id == TargetChannel.id)
            .where(SourceTargetRoute.source_id == raw_post.source_id, SourceTargetRoute.enabled.is_(True), TargetChannel.enabled.is_(True))
        ).all()
        if routes:
            return [target for _, target in routes]
        return db.scalars(select(TargetChannel).where(TargetChannel.enabled.is_(True))).all()

    async def run_autopublish(self, db: Session):
        from app.models import AppSetting
        setting = db.get(AppSetting, "global_auto_publish_enabled")
        if not setting or setting.value != "true":
            return

        posts = db.scalars(select(RawPost).where(RawPost.status == RawPostStatus.READY.value)).all()
        for post in posts:
            targets = [t for t in self._resolve_targets(db, post) if t.auto_publish_enabled and t.default_mode == "auto"]
            if not targets:
                db.add(ActionLog(action="auto_skip", entity_type="RawPost", entity_id=str(post.id), message="No auto targets"))
                db.commit()
                continue

            result = await self.ai_client.generate_news_post(post, db)
            if result.failed:
                # Техническая ошибка шлюза, а не редакционный отказ: пост остаётся
                # READY и будет обработан на следующем прогоне. Остальные посты
                # в этом прогоне не трогаем — шлюз для них тоже недоступен.
                db.add(ActionLog(action="ai_error", entity_type="RawPost", entity_id=str(post.id), message=result.reason))
                db.commit()
                break
            if not result.suitable or not result.text.strip():
                post.status = RawPostStatus.REJECTED.value
                post.ai_suitable = False
                post.ai_skip_reason = result.reason or "AI unsuitable"
                db.commit()
                continue

            generated = GeneratedPost(
                raw_post_id=post.id,
                generated_text=result.text.strip(),
                model_name=result.model_name,
                status=GeneratedPostStatus.DRAFT.value,
            )
            db.add(generated)
            post.status = RawPostStatus.GENERATED.value
            post.ai_suitable = True
            db.commit()
            db.refresh(generated)

            for target in targets:
                try:
                    await self.publisher.publish_generated_post(db, generated.id, target.id)
                except Exception as exc:
                    db.add(ActionLog(action="autopublish_error", entity_type="GeneratedPost", entity_id=str(generated.id), message=str(exc)))
                    db.commit()

    async def run_once(self, db: Session, skip_fetch: bool = False):
        if not skip_fetch:
            await self.fetch_new_posts(db)
        await self.process_ready_posts(db)
        await self.run_autopublish(db)
