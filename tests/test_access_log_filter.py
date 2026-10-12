"""Служебные запросы не должны вытеснять из лога то, что делает человек."""

import logging

from app.main import _SkipNoisyAccessLog


def _record(method: str, path: str) -> logging.LogRecord:
    # Ровно так uvicorn формирует строку access-лога.
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:0", method, path, "1.1", 200), None,
    )


def _kept(method: str, path: str) -> bool:
    return _SkipNoisyAccessLog().filter(_record(method, path))


def test_health_and_status_polling_are_dropped():
    assert not _kept("GET", "/health")
    assert not _kept("HEAD", "/health")
    assert not _kept("GET", "/api/scheduler-status")
    assert not _kept("GET", "/api/scheduler-status?t=1")


def test_real_requests_stay():
    assert _kept("GET", "/posts")
    assert _kept("POST", "/posts/340/delete")
    assert _kept("GET", "/api/scheduler-status-history")
    assert _kept("GET", "/sources/healthpills")
