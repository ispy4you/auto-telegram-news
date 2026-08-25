from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.services import settings_registry


class MediaStorageService:
    def __init__(self):
        self.settings = get_settings()

    def build_dir(self, source_id: int, post_id: int) -> Path:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        target = self.settings.media_root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}" / str(source_id) / str(post_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def validate_size(self, file_size: int | None) -> bool:
        if not file_size:
            return True
        max_mb = settings_registry.get("max_media_mb")
        return file_size <= max_mb * 1024 * 1024
