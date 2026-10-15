"""Проверки живости: /health не должен зависеть от базы, /health/db — должен."""

from app import main


def test_health_does_not_touch_db(client, monkeypatch):
    # Если /health начнёт ходить в базу, хостинг будет перезапускать контейнер
    # из-за недоступного кластера — поэтому ломаем сессию и ждём тот же ответ.
    def explode():
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(main, "SessionLocal", explode)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_db_reports_ok(client):
    response = client.get("/health/db")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["latency_ms"] >= 0


def test_health_db_hides_error_details(client, monkeypatch, caplog):
    # В исключениях SQLAlchemy попадается строка подключения с паролем,
    # поэтому наружу уходит только статус, а подробности — в лог.
    def explode():
        raise RuntimeError("postgresql://user:секрет@192.168.0.4:5432/db недоступна")

    monkeypatch.setattr(main, "SessionLocal", explode)
    with caplog.at_level("WARNING"):
        response = client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"status": "error"}
    assert "секрет" not in response.text
    assert "Проверка базы не прошла" in caplog.text


def test_favicon_is_served(client):
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
