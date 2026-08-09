from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SourceChannel
from app.services.telegram_reader import TelegramReaderService


class SourceReaderService:
    def __init__(self):
        self.settings = get_settings()
        self.telegram_reader = TelegramReaderService()

    async def fetch_source(self, db: Session, source: SourceChannel, limit: int | None = None) -> int:
        if limit is None:
            from app.services.prompt_settings import _get_setting
            try:
                limit = int(_get_setting(db, "default_lookback_limit", str(self.settings.default_lookback_limit)))
            except (ValueError, TypeError):
                limit = self.settings.default_lookback_limit
        return await self.telegram_reader.fetch_source(db, source, limit)
