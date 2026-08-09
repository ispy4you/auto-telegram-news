from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings


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
        from app.services.prompt_settings import _get_setting
        try:
            max_mb = int(_get_setting(None, "max_media_mb", str(self.settings.max_media_mb)))
        except (ValueError, TypeError):
            max_mb = self.settings.max_media_mb
        return file_size <= max_mb * 1024 * 1024
