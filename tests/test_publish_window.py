from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import app.services.telegram_publisher as telegram_publisher
from app.services.telegram_publisher import _is_within_window, _next_window_open_utc

TZ = "Europe/Moscow"


class _FixedDatetime(datetime):
    """Stand-in for datetime.now(tz) that always returns a fixed instant."""

    _fixed_utc: datetime

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._fixed_utc
        return cls._fixed_utc.astimezone(tz)


def _freeze(monkeypatch, hour: int, minute: int = 0):
    fixed_local = datetime(2024, 6, 10, hour, minute, tzinfo=ZoneInfo(TZ))
    frozen = type("Frozen", (_FixedDatetime,), {"_fixed_utc": fixed_local.astimezone(timezone.utc)})
    monkeypatch.setattr(telegram_publisher, "datetime", frozen)


def test_is_within_window_same_day_inside_range(monkeypatch):
    _freeze(monkeypatch, 13, 0)
    assert _is_within_window("09:00", "18:00", TZ) is True


def test_is_within_window_same_day_outside_range(monkeypatch):
    _freeze(monkeypatch, 20, 0)
    assert _is_within_window("09:00", "18:00", TZ) is False


def test_is_within_window_overnight_range_before_midnight(monkeypatch):
    _freeze(monkeypatch, 23, 0)
    assert _is_within_window("22:00", "06:00", TZ) is True


def test_is_within_window_overnight_range_after_midnight(monkeypatch):
    _freeze(monkeypatch, 2, 0)
    assert _is_within_window("22:00", "06:00", TZ) is True


def test_is_within_window_overnight_range_outside(monkeypatch):
    _freeze(monkeypatch, 12, 0)
    assert _is_within_window("22:00", "06:00", TZ) is False


def test_next_window_open_utc_later_today(monkeypatch):
    _freeze(monkeypatch, 9, 0)
    result = _next_window_open_utc("18:00", TZ)
    expected_local = datetime(2024, 6, 10, 18, 0, tzinfo=ZoneInfo(TZ))
    assert result == expected_local.astimezone(timezone.utc).replace(tzinfo=None)


def test_next_window_open_utc_rolls_to_tomorrow_when_already_passed(monkeypatch):
    _freeze(monkeypatch, 20, 0)
    result = _next_window_open_utc("18:00", TZ)
    expected_local = datetime(2024, 6, 11, 18, 0, tzinfo=ZoneInfo(TZ))
    assert result == expected_local.astimezone(timezone.utc).replace(tzinfo=None)
