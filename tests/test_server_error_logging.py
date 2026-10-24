"""Ошибка должна быть видна оператору, а не только в трейсбеке.

Тестировщик нажал «Проверить» на целевом канале и получил от браузера голую
строку «Internal Server Error»: ни что сломалось, ни где искать. Трейсбек при
этом лежал в логе контейнера, не привязанный к нажатию.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.models import ActionLog, Project

CODE = re.compile(r"<code>([0-9A-F]{6})</code>")
SECRET_URL = "postgresql://user:секрет@10.0.0.4:5432/db"


@pytest.fixture
def crashing_client(db_session, monkeypatch):
    """Клиент с маршрутом, который гарантированно падает.

    raise_server_exceptions=False — иначе TestClient поднимает исключение сам,
    и до ответа, который видит браузер, дело не доходит.
    """
    import app.main as main_module

    db_session.add(Project(id=1, name="Default", slug="default"))
    db_session.commit()

    async def boom():
        raise RuntimeError(f"подключение {SECRET_URL} потеряно")

    async def api_boom():
        raise RuntimeError("и здесь тоже")

    main_module.app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(main_module, "journal_session", sessionmaker(bind=db_session.get_bind()))
    main_module.app.add_api_route("/__boom", boom, methods=["GET"], include_in_schema=False)
    main_module.app.add_api_route("/api/__boom", api_boom, methods=["GET"], include_in_schema=False)
    try:
        yield TestClient(main_module.app, raise_server_exceptions=False, follow_redirects=False)
    finally:
        main_module.app.dependency_overrides.clear()
        main_module.app.router.routes = [
            route for route in main_module.app.router.routes
            if getattr(route, "path", "") not in {"/__boom", "/api/__boom"}
        ]


def test_the_operator_gets_a_code_instead_of_a_bare_five_hundred(crashing_client, caplog):
    with caplog.at_level("ERROR"):
        response = crashing_client.get("/__boom")

    assert response.status_code == 500
    match = CODE.search(response.text)
    assert match, "на странице ошибки нет кода"
    assert "журнал" in response.text
    # Наружу — только код: в тексте исключения попадаются пароли и внутренние адреса.
    assert "RuntimeError" not in response.text
    assert "секрет" not in response.text
    # А в лог — всё, вместе с кодом и тем, какой запрос упал.
    assert match.group(1) in caplog.text
    assert "/__boom" in caplog.text
    assert "RuntimeError" in caplog.text


def test_the_error_lands_in_the_panel_journal(crashing_client, db_session):
    code = CODE.search(crashing_client.get("/__boom").text).group(1)

    entry = db_session.query(ActionLog).filter(ActionLog.action == "server_error").one()
    assert entry.entity_id == code
    assert "GET /__boom" in entry.message
    assert "RuntimeError" in entry.message
    # Пароль из строки подключения в журнал не попадает.
    assert "секрет" not in entry.message
    assert "<скрыто>" in entry.message


def test_a_failed_journal_write_does_not_hide_the_page(crashing_client, monkeypatch, caplog):
    """База могла отвалиться — именно это и бывает причиной ошибки."""
    import app.main as main_module

    def broken_factory():
        raise RuntimeError("журнал недоступен")

    monkeypatch.setattr(main_module, "journal_session", broken_factory)
    with caplog.at_level("WARNING"):
        response = crashing_client.get("/__boom")

    assert response.status_code == 500
    assert CODE.search(response.text)
    assert "не удалось записать в журнал" in caplog.text


def _action_lines(caplog):
    """Только строки нашего логгера: в caplog попадают записи и чужих."""
    return [r.getMessage() for r in caplog.records if r.name == "app.web.request_log"]


def test_the_api_gets_json_instead_of_a_page(crashing_client):
    """Эти адреса читает разметка панели: HTML она разобрать не сможет."""
    response = crashing_client.get("/api/__boom")

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "error"
    assert re.fullmatch(r"[0-9A-F]{6}", body["code"])


def test_clicks_are_logged_with_the_address_and_whether_it_is_a_guest(client, caplog):
    with caplog.at_level("INFO", logger="app.web.request_log"):
        client.post("/login", data={"username": "admin", "password": "неверный"})

    lines = _action_lines(caplog)
    assert len(lines) == 1
    assert "POST /login" in lines[0]
    assert "гость" in lines[0]


def test_successful_pages_are_not_logged(client, caplog):
    """Окно лога на хостинге — двести строк, забивать его открытием страниц нельзя."""
    with caplog.at_level("INFO", logger="app.web.request_log"):
        client.get("/login")

    assert _action_lines(caplog) == []


def test_a_rejected_request_is_logged(client, caplog):
    with caplog.at_level("INFO", logger="app.web.request_log"):
        response = client.post("/projects", data={"name": "без токена"})

    assert response.status_code == 403
    assert any("403" in line for line in _action_lines(caplog))
