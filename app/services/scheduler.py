import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, PublishJob, PublishJobStatus, RawPost, RawPostStatus
from app.services.news_pipeline import NewsPipelineService
from app.services.telegram_publisher import TelegramPublisherService

_MAX_RETRY_ATTEMPTS = 3
_JOB_ID = "main_pipeline"


class SchedulerService:
    def __init__(self, interval_seconds: int, listener=None):
        self.scheduler = AsyncIOScheduler()
        self.pipeline = NewsPipelineService()
        self.publisher = TelegramPublisherService()
        self.interval_seconds = interval_seconds
        self.listener = listener  # TelegramEventListenerService | None
        self._lock = asyncio.Lock()
        self.last_run_at: datetime | None = None
        self.is_running: bool = False
        self._last_draft_notified: int = 0

    @property
    def next_run_at(self) -> datetime | None:
        job = self.scheduler.get_job(_JOB_ID)
        return job.next_run_time if job else None

    async def _retry_failed_jobs(self, db):
        failed_jobs = db.scalars(
            select(PublishJob)
            .where(PublishJob.status == PublishJobStatus.FAILED.value, PublishJob.attempts < _MAX_RETRY_ATTEMPTS)
        ).all()
        for job in failed_jobs:
            generated = db.get(GeneratedPost, job.generated_post_id)
            if not generated or generated.status == GeneratedPostStatus.PUBLISHED.value:
                continue
            generated.status = GeneratedPostStatus.APPROVED.value
            generated.publish_error = None
            raw = db.get(RawPost, generated.raw_post_id)
            if raw:
                raw.status = RawPostStatus.GENERATED.value
            db.delete(job)
            db.flush()
            try:
                await self.publisher.publish_generated_post(db, generated.id, job.target_channel_id)
            except Exception:
                pass

    async def _process_scheduled_jobs(self, db):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        due_jobs = db.scalars(
            select(PublishJob)
            .where(
                PublishJob.status == PublishJobStatus.PENDING.value,
                PublishJob.scheduled_at <= now,
            )
        ).all()
        for job in due_jobs:
            generated = db.get(GeneratedPost, job.generated_post_id)
            if not generated or generated.status not in (
                GeneratedPostStatus.SCHEDULED.value,
                GeneratedPostStatus.APPROVED.value,
            ):
                continue
            generated.status = GeneratedPostStatus.APPROVED.value
            target_channel_id = job.target_channel_id
            db.delete(job)
            db.flush()
            try:
                await self.publisher.publish_generated_post(db, generated.id, target_channel_id)
            except Exception:
                pass

    async def _check_draft_notification(self, db):
        from app.services.prompt_settings import _get_setting
        try:
            threshold = int(_get_setting(db, "notify_draft_threshold", "0"))
        except (ValueError, TypeError):
            threshold = 0
        if threshold <= 0:
            return

        count = db.scalar(
            select(func.count()).select_from(GeneratedPost)
            .where(GeneratedPost.status.in_([GeneratedPostStatus.DRAFT.value, GeneratedPostStatus.APPROVED.value]))
        ) or 0

        if count >= threshold and count > self._last_draft_notified:
            self._last_draft_notified = count
            from app.services.notifier import notify_operator
            await notify_operator(
                db,
                f"📬 <b>Накопилось черновиков: {count}</b>\n\nПорог: {threshold}. Требуется проверка в панели управления.",
            )
        elif count < threshold:
            self._last_draft_notified = 0

    async def _safe_run(self):
        if not self._lock.locked():
            async with self._lock:
                self.is_running = True
                self.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
                with SessionLocal() as db:
                    try:
                        skip_fetch = bool(self.listener and self.listener.is_active)
                        before_total = db.scalar(select(func.count()).select_from(RawPost)) or 0

                        await self.pipeline.run_once(db, skip_fetch=skip_fetch)
                        await self._retry_failed_jobs(db)
                        await self._process_scheduled_jobs(db)
                        await self._check_draft_notification(db)

                        after_total = db.scalar(select(func.count()).select_from(RawPost)) or 0
                        fetched = max(0, after_total - before_total)
                        fetch_mode = "event" if skip_fetch else "poll"

                        db.add(ActionLog(
                            action="scheduler_run",
                            entity_type="Scheduler",
                            entity_id="auto",
                            message=f"Автосбор завершён [{fetch_mode}]. Новых постов: {fetched}. Интервал: {self.interval_seconds}с",
                        ))
                        db.commit()
                    except Exception as exc:
                        from app.services.prompt_settings import _get_setting
                        if _get_setting(db, "notify_on_error", "false") == "true":
                            from app.services.notifier import notify_operator
                            try:
                                await notify_operator(
                                    db,
                                    f"⚠️ <b>Ошибка пайплайна</b>\n\n<code>{type(exc).__name__}: {exc}</code>",
                                )
                            except Exception:
                                pass
                        db.add(ActionLog(
                            action="scheduler_error",
                            entity_type="Scheduler",
                            entity_id="auto",
                            message=f"Ошибка: {exc}",
                        ))
                        db.commit()
                    finally:
                        self.is_running = False

    async def trigger_run(self):
        await self._safe_run()

    def update_interval(self, seconds: int):
        self.interval_seconds = seconds
        self.scheduler.reschedule_job(_JOB_ID, trigger="interval", seconds=seconds)

    def start(self):
        self.scheduler.add_job(self._safe_run, "interval", seconds=self.interval_seconds, max_instances=1, id=_JOB_ID)
        self.scheduler.start()

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
