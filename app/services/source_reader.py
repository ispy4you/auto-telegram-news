from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SourceChannel
from app.services.rss_reader import RssReaderService
from app.services.telegram_reader import TelegramReaderService


class SourceReaderService:
    def __init__(self):
        self.settings = get_settings()
        self.telegram_reader = TelegramReaderService()
        self.rss_reader = RssReaderService()

    async def fetch_source(self, db: Session, source: SourceChannel, limit: int | None = None) -> int:
        limit = limit or self.settings.default_lookback_limit
        if source.source_type == "rss":
            return await self.rss_reader.fetch_source(db, source, limit)
        return await self.telegram_reader.fetch_source(db, source, limit)
