from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SourceChannel
from app.services.telegram_reader import TelegramReaderService


class SourceReaderService:
    def __init__(self):
        self.settings = get_settings()
        self.telegram_reader = TelegramReaderService()

    async def fetch_source(self, db: Session, source: SourceChannel, limit: int | None = None) -> int:
        limit = limit or self.settings.default_lookback_limit
        return await self.telegram_reader.fetch_source(db, source, limit)
