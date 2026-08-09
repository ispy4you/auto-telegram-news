from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.telegram_reader import TelegramReaderService


def test_write_post_stores_naive_utc_from_aware_message_date(db_session, source):
    reader = TelegramReaderService()
    msk = timezone(timedelta(hours=3))
    aware_date = datetime(2026, 8, 9, 23, 0, 0, tzinfo=msk)
    msg = SimpleNamespace(id=101, grouped_id=None, message="hello", date=aware_date)

    post = reader._write_post(db_session, source, msg, None, [])

    assert post is not None
    assert post.published_at_source.tzinfo is None
    assert post.published_at_source == datetime(2026, 8, 9, 20, 0, 0)
