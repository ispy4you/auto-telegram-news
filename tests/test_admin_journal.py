"""Действия администратора попадают в свой журнал.

Лог контейнера живёт до перезапуска и показывает последние двести строк:
по нему нельзя ответить, кто и когда поменял настройку неделю назад.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import ActionLog, AdminLog, SourceChannel
from app.web import admin_journal
from app.web.routes.common import utcnow


def _entries(db, action: str | None = None) -> list[AdminLog]:
    query = select(AdminLog).order_by(AdminLog.id)
    if action:
        query = query.where(AdminLog.action == action)
    return list(db.scalars(query).all())


def _rows(page) -> str:
    """Только тело таблицы: названия событий есть ещё и в списке фильтра."""
    return page.text.split("<tbody>")[1].split("</tbody>")[0]


def _one(db, action: str) -> AdminLog:
    found = _entries(db, action)
    assert len(found) == 1, f"записей {action}: {len(found)}"
    return found[0]


# ── Вход ────────────────────────────────────────────────────────────────────

def test_a_login_is_recorded(client, db_session, csrf):
    client.post("/login", data={
        "username": "admin", "password": "change_me", "csrf_token": csrf(client),
    })

    entry = _one(db_session, "login")
    assert entry.message == "Вход в панель"
    assert entry.path == "/login"
    assert entry.status == 302


def test_a_wrong_password_is_recorded_as_a_guest(client, db_session):
    client.post("/login", data={"username": "admin", "password": "неверный"})

    entry = _one(db_session, "login_failed")
    assert entry.authenticated is False
    assert entry.actor


def test_the_password_itself_never_reaches_the_journal(client, db_session):
    client.post("/login", data={"username": "admin", "password": "очень-секретный"})

    assert "секретный" not in " ".join(e.message + e.path for e in _entries(db_session))


def test_a_lockout_is_recorded_separately(client, db_session):
    for _ in range(6):
        client.post("/login", data={"username": "admin", "password": "неверный"})

    assert len(_entries(db_session, "login_failed")) == 5
    assert len(_entries(db_session, "login_locked")) == 1


# ── Нажатия в панели ────────────────────────────────────────────────────────

def test_a_settings_change_is_recorded(logged_in, db_session, csrf):
    logged_in.post("/settings", data={
        "csrf_token": csrf(logged_in, "/settings"), "duplicate_threshold": "90",
    })

    entry = _one(db_session, "settings_save")
    assert entry.message == "Настройки сохранены"
    assert entry.authenticated is True


def test_a_deleted_source_is_named_by_its_number(logged_in, db_session, csrf, source):
    logged_in.post(f"/sources/{source.id}/delete", data={"csrf_token": csrf(logged_in, "/sources")})

    assert db_session.get(SourceChannel, source.id) is None
    assert _one(db_session, "source_delete").message == f"Источник #{source.id} удалён"


def test_a_refusal_is_visible_in_the_entry(client, db_session):
    """POST без токена отбивает защита от подделки — это тоже надо видеть."""
    response = client.post("/projects", data={"name": "без токена"})

    assert response.status_code == 403
    assert _one(db_session, "project_add").message.endswith("— отказ 403")


def test_a_crashed_action_is_recorded_too(client, db_session, csrf, monkeypatch):
    """Упавшее нажатие — первое, что ищут в журнале."""
    import app.main as main_module
    from sqlalchemy.orm import sessionmaker

    async def boom():
        raise RuntimeError("упало")

    # Обработчик ошибок в main пишет своим журналом — его тоже в тестовую базу.
    monkeypatch.setattr(main_module, "journal_session", sessionmaker(bind=db_session.get_bind()))
    main_module.app.add_api_route("/__boom", boom, methods=["POST"], include_in_schema=False)
    try:
        with pytest.raises(RuntimeError):
            client.post("/__boom", data={"csrf_token": csrf(client)})
    finally:
        main_module.app.router.routes = [
            route for route in main_module.app.router.routes
            if getattr(route, "path", "") != "/__boom"
        ]

    entry = _one(db_session, "other")
    assert entry.status == 500
    assert "/__boom" in entry.message


def test_an_unknown_path_is_still_recorded(client, db_session):
    """Новый обработчик не должен молча выпадать из журнала."""
    client.post("/зачем-то-новое", data={})

    entry = _one(db_session, "other")
    assert "/зачем-то-новое" in entry.message


def test_opening_pages_writes_nothing(logged_in, db_session):
    before = len(_entries(db_session))  # сам вход фикстуры уже записан

    for path in ("/", "/posts", "/settings", "/logs", "/logs/admin"):
        assert logged_in.get(path).status_code == 200

    assert len(_entries(db_session)) == before


def test_requests_the_page_sends_by_itself_are_not_clicks(logged_in, db_session, csrf):
    """Восстановление файлов панель запускает сама при открытии поста."""
    before = len(_entries(db_session))

    logged_in.post("/posts/404/restore-media", data={"csrf_token": csrf(logged_in, "/posts")})

    assert len(_entries(db_session)) == before


def test_liveness_probes_do_not_reach_the_journal(client, db_session):
    """Хостинг дёргает /health методом HEAD каждые несколько секунд.

    FastAPI отвечает на HEAD кодом 405, и проверка «метод не GET» затягивала
    в журнал по записи на каждый такой стук — за сутки тысячи строк.
    """
    for _ in range(3):
        client.head("/health")
    client.options("/health")

    assert _entries(db_session) == []


def test_a_very_long_path_does_not_blow_up_the_entry(client, db_session):
    """Длину пути задаёт тот, кто отправил запрос, а не мы."""
    client.post("/" + "ы" * 5000)

    entry = _one(db_session, "other")
    assert len(entry.message) < 600
    assert len(entry.path) <= 255


def test_the_pipeline_journal_stays_clean(logged_in, db_session, csrf):
    logged_in.post("/settings", data={"csrf_token": csrf(logged_in, "/settings")})

    assert (db_session.scalar(select(func.count()).select_from(ActionLog)) or 0) == 0


# ── Названия событий ────────────────────────────────────────────────────────

def test_an_event_name_drops_the_object_number():
    assert admin_journal.label("source_delete") == "Источник удалён"
    assert admin_journal.label("media_delete") == "У поста удалён файл"
    assert admin_journal.label("login_locked") == "Вход заблокирован: слишком много попыток"
    assert admin_journal.label("other") == "Прочее"
    assert admin_journal.label("неизвестное") == "неизвестное"


def test_similar_paths_do_not_mix_up():
    assert admin_journal.describe("POST", "/settings")[0] == "settings_save"
    assert admin_journal.describe("POST", "/settings/reset")[0] == "settings_reset"
    assert admin_journal.describe("POST", "/posts/7/media")[0] == "media_upload"
    assert admin_journal.describe("POST", "/posts/7/media/3/delete") == (
        "media_delete", "У поста #7 удалён файл #3",
    )
    assert admin_journal.describe("POST", "/prompts/4")[0] == "prompt_edit"
    assert admin_journal.describe("POST", "/prompts/4/delete")[0] == "prompt_delete"


# ── Страница ────────────────────────────────────────────────────────────────

def test_the_page_shows_actions_and_the_filter_narrows_them(logged_in, db_session, csrf):
    logged_in.post("/settings", data={"csrf_token": csrf(logged_in, "/settings")})

    rows = _rows(logged_in.get("/logs/admin"))
    assert "Настройки сохранены" in rows
    assert "Вход в панель" in rows
    # В таблице — фразы, а не коды событий.
    assert "settings_save" not in rows

    only_login = _rows(logged_in.get("/logs/admin?action=login"))
    assert "Вход в панель" in only_login
    assert "Настройки сохранены" not in only_login


def test_the_filter_searches_by_address_too(logged_in, db_session, csrf):
    logged_in.post("/settings", data={"csrf_token": csrf(logged_in, "/settings")})

    assert "Настройки сохранены" in _rows(logged_in.get("/logs/admin?q=testclient"))
    assert "Действий нет" in _rows(logged_in.get("/logs/admin?q=10.0.0.1"))


def test_the_two_journals_stay_on_their_own_pages(logged_in, db_session, csrf):
    db_session.add(ActionLog(
        action="publish_success", entity_type="GeneratedPost", entity_id="1", message="Событие робота",
    ))
    db_session.commit()
    logged_in.post("/settings", data={"csrf_token": csrf(logged_in, "/settings")})

    assert "Событие робота" not in _rows(logged_in.get("/logs/admin"))
    assert "Настройки сохранены" not in _rows(logged_in.get("/logs"))


# ── Чистка ──────────────────────────────────────────────────────────────────

def test_old_admin_entries_are_pruned(db_session):
    from app.services.retention import prune_admin_logs

    for age in (40, 31, 29, 0):
        db_session.add(AdminLog(
            action="login", actor="1.2.3.4", authenticated=True, method="POST",
            path="/login", status=302, message="Вход в панель",
            created_at=utcnow() - timedelta(days=age),
        ))
    db_session.commit()

    assert prune_admin_logs(db_session) == 2
    assert (db_session.scalar(select(func.count()).select_from(AdminLog)) or 0) == 2


# ── Полнота таблицы ─────────────────────────────────────────────────────────

def test_every_mutating_route_has_a_phrase():
    """Новый обработчик должен получить фразу, а не остаться «прочим».

    Журнал пишется из middleware по пути запроса, так что про добавленную
    кнопку он узнаёт сам — но назвать её по-русски может только таблица
    соответствий. Этот тест и напоминает дописать туда строку.
    """
    import re

    from app.web.routes import router

    nameless = []
    for route in router.routes:
        if getattr(route, "methods", set()) <= {"GET", "HEAD"}:
            continue
        described = admin_journal.describe("POST", re.sub(r"\{[^}]+\}", "7", route.path))
        if described and described[0] == "other":
            nameless.append(route.path)

    assert nameless == [], "эти действия попадут в журнал без названия"
