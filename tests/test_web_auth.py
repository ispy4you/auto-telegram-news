"""Панель торчит в открытый интернет — вход и защита от CSRF проверяются здесь.

TestClient создаётся без контекстного менеджера: так Starlette не запускает
lifespan, а значит ни миграции, ни планировщик в тестах не поднимаются.
Это стало возможно после переноса подготовки окружения из импорта в lifespan.
"""

import re

from fastapi.testclient import TestClient

from app.models import AppSetting

CSRF_INPUT = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _csrf(client: TestClient, path: str = "/login") -> str:
    page = client.get(path)
    match = CSRF_INPUT.search(page.text)
    assert match, f"на странице {path} нет поля csrf_token"
    return match.group(1)


def _login(client: TestClient, username: str = "admin", password: str = "change_me"):
    return client.post("/login", data={
        "username": username,
        "password": password,
        "csrf_token": _csrf(client),
    })


# ── Авторизация ─────────────────────────────────────────────────────────────

def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_wrong_password_does_not_authenticate(client):
    response = _login(client, password="неверный")

    assert response.headers["location"] == "/login?error=1"
    assert client.get("/").status_code == 302


def test_non_ascii_password_is_rejected_not_crashed(client):
    """hmac.compare_digest на строках падает с TypeError на не-ASCII символах:
    пароль с кириллицей раньше давал не отказ во входе, а пятисотку."""
    response = _login(client, password="пароль-с-кириллицей")

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=1"


def test_correct_credentials_open_the_panel(client):
    response = _login(client)

    assert response.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_brute_force_is_locked_out_after_five_attempts(client):
    for _ in range(5):
        _login(client, password="неверный")

    response = _login(client, password="неверный")
    assert response.headers["location"] == "/login?error=locked"

    # Блокировка не обходится верным паролем.
    assert _login(client).headers["location"] == "/login?error=locked"


# ── CSRF ────────────────────────────────────────────────────────────────────

def test_post_without_csrf_token_is_rejected(client):
    _login(client)

    response = client.post("/settings", data={"duplicate_threshold": "90"})

    assert response.status_code == 403


def test_post_with_foreign_csrf_token_is_rejected(client):
    _login(client)

    response = client.post("/settings", data={
        "duplicate_threshold": "90",
        "csrf_token": "a" * 64,
    })

    assert response.status_code == 403


def test_login_form_itself_is_exempt_from_csrf(client):
    """Иначе первый вход был бы невозможен: сессии ещё нет."""
    response = client.post("/login", data={"username": "admin", "password": "change_me"})

    assert response.status_code == 302
    assert response.headers["location"] == "/"


# ── Форма настроек целиком ──────────────────────────────────────────────────

def test_settings_form_stores_normalised_values(client, db_session):
    _login(client)
    token = _csrf(client, "/settings")

    response = client.post("/settings", data={
        "csrf_token": token,
        "duplicate_threshold": "1000",       # выше границы — обрежется до 100
        "semantic_threshold": "0.85",
        "operator_chat_id": "  @ops  ",      # пробелы уйдут
        "global_auto_publish_enabled": "on",
    })

    assert response.status_code == 302
    stored = {row.key: row.value for row in db_session.query(AppSetting).all()}
    assert stored["duplicate_threshold"] == "100"
    assert stored["semantic_threshold"] == "0.85"
    assert stored["operator_chat_id"] == "@ops"
    assert stored["global_auto_publish_enabled"] == "true"


def test_unchecked_checkbox_turns_the_flag_off(client, db_session):
    _login(client)
    db_session.add(AppSetting(key="global_auto_publish_enabled", value="true"))
    db_session.commit()

    client.post("/settings", data={"csrf_token": _csrf(client, "/settings")})

    row = db_session.get(AppSetting, "global_auto_publish_enabled")
    assert row.value == "false", "браузер не присылает снятые галочки"


def test_blank_secret_keeps_the_stored_value(client, db_session):
    _login(client)
    db_session.add(AppSetting(key="telegram_bot_token", value="секрет"))
    db_session.commit()

    client.post("/settings", data={
        "csrf_token": _csrf(client, "/settings"),
        "telegram_bot_token": "",
    })

    assert db_session.get(AppSetting, "telegram_bot_token").value == "секрет"
