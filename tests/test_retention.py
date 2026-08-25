from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models import ActionLog, AppSetting
from app.services.retention import prune_action_logs


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _add_log(db, age_days: int) -> None:
    db.add(ActionLog(
        action="scheduler_run",
        entity_type="Scheduler",
        entity_id="auto",
        message="x",
        created_at=_utcnow() - timedelta(days=age_days),
    ))


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(ActionLog)) or 0


def test_old_records_are_removed_and_recent_kept(db_session):
    _add_log(db_session, 40)
    _add_log(db_session, 31)
    _add_log(db_session, 29)
    _add_log(db_session, 0)
    db_session.commit()

    deleted = prune_action_logs(db_session)

    assert deleted == 2
    assert _count(db_session) == 2


def test_retention_period_is_configurable(db_session):
    _add_log(db_session, 10)
    _add_log(db_session, 3)
    db_session.add(AppSetting(key="action_log_retention_days", value="7"))
    db_session.commit()

    prune_action_logs(db_session)

    assert _count(db_session) == 1


def test_zero_days_disables_cleanup(db_session):
    _add_log(db_session, 500)
    db_session.add(AppSetting(key="action_log_retention_days", value="0"))
    db_session.commit()

    assert prune_action_logs(db_session) == 0
    assert _count(db_session) == 1
