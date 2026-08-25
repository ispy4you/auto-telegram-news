"""Чистка журнала действий.

action_logs пишется на каждое заметное событие и раньше рос бесконечно.
Журнал — расходный материал: он нужен, чтобы разобраться в недавнем, а не
чтобы хранить историю за годы.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import ActionLog
from app.services import settings_registry

logger = logging.getLogger(__name__)

def prune_action_logs(db: Session) -> int:
    """Удаляет записи журнала старше настроенного срока. 0 дней — не чистить."""
    days = settings_registry.get("action_log_retention_days", db)
    if days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    deleted = db.execute(delete(ActionLog).where(ActionLog.created_at < cutoff)).rowcount or 0
    db.commit()
    if deleted:
        logger.info("Журнал: удалено %s записей старше %s дн.", deleted, days)
    return deleted
