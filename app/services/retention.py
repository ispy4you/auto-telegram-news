"""Чистка журналов.

action_logs пишется на каждое заметное событие и раньше рос бесконечно.
Журнал — расходный материал: он нужен, чтобы разобраться в недавнем, а не
чтобы хранить историю за годы. То же и для admin_logs с действиями человека:
срок у них общий, настройка одна.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import ActionLog, AdminLog
from app.services import settings_registry

logger = logging.getLogger(__name__)


def _prune(db: Session, model, what: str) -> int:
    """Удаляет записи старше настроенного срока. 0 дней — не чистить."""
    days = settings_registry.get("action_log_retention_days", db)
    if days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    deleted = db.execute(delete(model).where(model.created_at < cutoff)).rowcount or 0
    db.commit()
    if deleted:
        logger.info("%s: удалено %s записей старше %s дн.", what, deleted, days)
    return deleted


def prune_action_logs(db: Session) -> int:
    return _prune(db, ActionLog, "Журнал")


def prune_admin_logs(db: Session) -> int:
    return _prune(db, AdminLog, "Журнал действий")
