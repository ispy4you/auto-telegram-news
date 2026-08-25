"""Страница настроек должна отрисовываться целиком — включая карточку входа."""

from app.config import get_settings


def _with_telegram_keys(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_api_id", 1234, raising=False)
    monkeypatch.setattr(settings, "telegram_api_hash", "hash", raising=False)


def test_settings_page_renders(logged_in):
    assert logged_in.get("/settings").status_code == 200


def test_login_card_shows_the_wizard_when_api_keys_are_set(logged_in, monkeypatch):
    _with_telegram_keys(monkeypatch)

    page = logged_in.get("/settings").text

    for marker in ("tg-state-title", "tg-wizard", "tg-pick-qr", "tg-pick-phone", "tg-success"):
        assert marker in page, f"в карточке входа нет {marker}"
    assert "Настройки</b> → <b>Устройства" in page, "нет пошаговой инструкции по QR"


def test_login_card_asks_for_api_keys_when_they_are_missing(logged_in, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_api_id", None, raising=False)
    monkeypatch.setattr(settings, "telegram_api_hash", None, raising=False)

    page = logged_in.get("/settings").text

    assert "my.telegram.org" in page
    assert "tg-wizard" not in page
