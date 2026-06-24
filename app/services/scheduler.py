import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import SessionLocal
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, PublishJob, PublishJobStatus, RawPost, RawPostStatus
from app.services.news_pipeline import NewsPipelineService
from app.services.telegram_publisher import TelegramPublisherService

_MAX_RETRY_ATTEMPTS = 3
_JOB_ID = "main_pipeline"


class SchedulerService:
    def __init__(self, interval_seconds: int):
        self.scheduler = AsyncIOScheduler()
        self.pipeline = NewsPipelineService()
        self.publisher = TelegramPublisherService()
        self.interval_seconds = interval_seconds
        self._lock = asyncio.Lock()
        self.last_run_at: datetime | None = None
        self.is_running: bool = False

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

    async def _safe_run(self):
        if not self._lock.locked():
            async with self._lock:
                self.is_running = True
                self.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
                with SessionLocal() as db:
                    try:
                        from sqlalchemy import func
                        before_total = db.scalar(select(func.count()).select_from(RawPost)) or 0

                        await self.pipeline.run_once(db)
                        await self._retry_failed_jobs(db)

                        after_total = db.scalar(select(func.count()).select_from(RawPost)) or 0
                        fetched = max(0, after_total - before_total)

                        db.add(ActionLog(
                            action="scheduler_run",
                            entity_type="Scheduler",
                            entity_id="auto",
                            message=f"Автосбор завершён. Новых постов: {fetched}. Интервал: {self.interval_seconds}с",
                        ))
                        db.commit()
                    finally:
                        self.is_running = False

    def update_interval(self, seconds: int):
        self.interval_seconds = seconds
        self.scheduler.reschedule_job(_JOB_ID, trigger="interval", seconds=seconds)

    def start(self):
        self.scheduler.add_job(self._safe_run, "interval", seconds=self.interval_seconds, max_instances=1, id=_JOB_ID)
        self.scheduler.start()

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
