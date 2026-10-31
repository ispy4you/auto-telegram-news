"""Статистика считает по текущему проекту и по часовому поясу панели.

Повод: страница была единственной в панели, которая не спрашивала, какой
проект открыт, — цифры всех проектов складывались в одну таблицу. А график
за две недели подписывал дни местным временем, но раскладывал публикации по
суткам UTC, поэтому ночной пост уезжал в предыдущий столбик.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models import (
    GeneratedPost,
    GeneratedPostStatus,
    Project,
    RawPost,
    RawPostStatus,
    SourceChannel,
    TargetChannel,
)
from app.services import statistics

MOSCOW = ZoneInfo("Europe/Moscow")


@pytest.fixture
def projects(db_session):
    """Два проекта: цифры одного не должны попадать в другой.

    Проект с id 1 мог уже появиться из фикстуры веб-клиента — тогда берём
    тот, что есть, а не ломаемся об уникальный slug.
    """
    for pid, slug in ((1, "first"), (2, "second")):
        if db_session.get(Project, pid) is None:
            db_session.add(Project(id=pid, name=slug, slug=slug))
    db_session.commit()


@pytest.fixture
def make_source(db_session, projects):
    counter = {"n": 0}

    def _make(title: str, project_id: int = 1) -> SourceChannel:
        # Имя канала уникально, а названия в этих тестах нарочно повторяются.
        counter["n"] += 1
        handle = f"{title.lower()}{counter['n']}"
        src = SourceChannel(
            title=title,
            username=handle,
            url=f"https://t.me/{handle}",
            project_id=project_id,
        )
        db_session.add(src)
        db_session.commit()
        db_session.refresh(src)
        return src

    return _make


@pytest.fixture
def make_post(db_session):
    counter = {"n": 0}

    def _make(source: SourceChannel, status: str, published_at: datetime | None = None,
              target: TargetChannel | None = None) -> RawPost:
        counter["n"] += 1
        raw = RawPost(
            source_id=source.id,
            telegram_message_id=counter["n"],
            text_hash=f"h{counter['n']}",
            original_text="новость",
            status=status,
        )
        db_session.add(raw)
        db_session.flush()
        if status == RawPostStatus.PUBLISHED.value or published_at is not None:
            db_session.add(GeneratedPost(
                raw_post_id=raw.id,
                generated_text="текст",
                status=GeneratedPostStatus.PUBLISHED.value,
                published_at=published_at,
                target_channel_id=target.id if target else None,
            ))
        db_session.commit()
        return raw

    return _make


def test_the_funnel_counts_only_the_current_project(db_session, make_source, make_post):
    mine, theirs = make_source("Mine", 1), make_source("Theirs", 2)
    make_post(mine, RawPostStatus.PUBLISHED.value)
    make_post(mine, RawPostStatus.DUPLICATE.value)
    make_post(theirs, RawPostStatus.PUBLISHED.value)

    assert statistics.funnel(db_session, 1) == {
        "fetched": 2, "unique": 1, "generated": 1, "published": 1,
    }


def test_the_funnel_without_a_project_counts_everything(db_session, make_source, make_post):
    """Проекта может не быть вовсе — тогда считаем всё, как раньше."""
    make_post(make_source("Mine", 1), RawPostStatus.PUBLISHED.value)
    make_post(make_source("Theirs", 2), RawPostStatus.PUBLISHED.value)

    assert statistics.funnel(db_session, None)["fetched"] == 2


def test_a_night_publication_lands_in_the_local_day(db_session, make_source, make_post):
    """Полпервого ночи в Москве — это уже сегодня, хотя в UTC ещё вчера."""
    local_today = datetime.now(MOSCOW).date()
    half_past_midnight = datetime.combine(local_today, time(0, 30), MOSCOW)
    make_post(
        make_source("Mine"),
        RawPostStatus.PUBLISHED.value,
        published_at=half_past_midnight.astimezone(timezone.utc).replace(tzinfo=None),
    )

    labels, counts = statistics.published_per_day(db_session, 1, "Europe/Moscow")

    assert len(labels) == len(counts) == statistics.DAYS
    assert labels[-1] == local_today.strftime("%d.%m")
    assert counts[-1] == 1, "ночной пост должен попасть в сегодняшний столбик"
    assert counts[-2] == 0, "и не должен задваиваться во вчерашнем"


def test_the_chart_covers_two_weeks_and_fills_empty_days(db_session, make_source, make_post):
    local_today = datetime.now(MOSCOW).date()
    make_post(
        make_source("Mine"),
        RawPostStatus.PUBLISHED.value,
        published_at=datetime.combine(local_today - timedelta(days=3), time(12), MOSCOW)
        .astimezone(timezone.utc).replace(tzinfo=None),
    )

    labels, counts = statistics.published_per_day(db_session, 1, "Europe/Moscow")

    assert counts == [0] * 10 + [1] + [0] * 3
    assert labels[0] == (local_today - timedelta(days=13)).strftime("%d.%m")


def test_the_chart_leaves_out_other_projects(db_session, make_source, make_post):
    moment = datetime.now(timezone.utc).replace(tzinfo=None)
    make_post(make_source("Theirs", 2), RawPostStatus.PUBLISHED.value, published_at=moment)

    _, counts = statistics.published_per_day(db_session, 1, "Europe/Moscow")

    assert sum(counts) == 0


def test_the_accept_rate_is_the_share_that_went_through(db_session, make_source, make_post):
    source = make_source("Lenta")
    for _ in range(3):
        make_post(source, RawPostStatus.PUBLISHED.value)
    make_post(source, RawPostStatus.REJECTED.value)

    assert statistics.top_sources(db_session, 1) == [
        {"title": "Lenta", "published": 3, "rejected": 1, "rate": 75},
    ]


def test_two_sources_with_one_name_keep_their_own_numbers(db_session, make_source, make_post):
    """Считаем по id: одинаковые названия складывали отказы в один источник."""
    first, second = make_source("Lenta"), make_source("Lenta")
    for _ in range(2):
        make_post(first, RawPostStatus.PUBLISHED.value)
    make_post(second, RawPostStatus.PUBLISHED.value)
    make_post(second, RawPostStatus.REJECTED.value)

    assert statistics.top_sources(db_session, 1) == [
        {"title": "Lenta", "published": 2, "rejected": 0, "rate": 100},
        {"title": "Lenta", "published": 1, "rejected": 1, "rate": 50},
    ]


def test_sources_of_other_projects_are_not_in_the_table(db_session, make_source, make_post):
    make_post(make_source("Theirs", 2), RawPostStatus.PUBLISHED.value)

    assert statistics.top_sources(db_session, 1) == []


def test_targets_are_counted_per_project(db_session, make_source, make_post):
    mine = TargetChannel(title="Mine", chat_id="@mine", project_id=1)
    theirs = TargetChannel(title="Theirs", chat_id="@theirs", project_id=2)
    db_session.add_all([mine, theirs])
    db_session.commit()
    source = make_source("Lenta")
    make_post(source, RawPostStatus.PUBLISHED.value, target=mine)
    make_post(source, RawPostStatus.PUBLISHED.value, target=mine)
    make_post(source, RawPostStatus.PUBLISHED.value, target=theirs)

    assert statistics.published_per_target(db_session, 1) == [{"title": "Mine", "count": 2}]


def test_the_page_opens_and_shows_the_numbers(logged_in, db_session, make_source, make_post):
    make_post(make_source("Lenta"), RawPostStatus.PUBLISHED.value)

    page = logged_in.get("/stats")

    assert page.status_code == 200
    assert "Lenta" in page.text
