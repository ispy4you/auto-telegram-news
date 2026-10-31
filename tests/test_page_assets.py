"""Код страниц лежит отдельными файлами — и страницы их действительно зовут.

Повод: из шаблонов уехало больше восьмисот строк CSS и JS. Опечатка в пути
после такого переезда ничего не ломает на вид — страница открывается, тесты
проходят, а редактор или мастер входа на ней мёртвый. Поэтому проверяем оба
конца: и ссылку в разметке, и что по этой ссылке что-то отдают.
"""

from pathlib import Path

import pytest

from app.models import GeneratedPost, RawPost

_STATIC = Path("app/web/static")
_TEMPLATES = Path("app/web/templates")

#: Всё, что страницы подключают по имени.
ASSETS = (
    "/static/app.css",
    "/static/app.js",
    "/static/post-editor.css",
    "/static/post-editor.js",
    "/static/telegram-login.js",
)


@pytest.fixture
def post(db_session, source):
    raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="raw")
    db_session.add(raw)
    db_session.flush()
    db_session.add(GeneratedPost(raw_post_id=raw.id, generated_text="Новость дня"))
    db_session.commit()
    return raw


@pytest.mark.parametrize("asset", ASSETS)
def test_the_files_are_served(logged_in, asset):
    response = logged_in.get(asset)

    assert response.status_code == 200, f"{asset} не отдаётся"
    assert response.text.strip(), f"{asset} пустой"
    assert _STATIC.joinpath(Path(asset).name).is_file(), f"{asset} нет в репозитории"


@pytest.mark.parametrize("asset", ("/static/app.css", "/static/app.js"))
def test_every_page_gets_the_shared_chrome(logged_in, asset):
    """Общая рамка панели — на любой странице, она же в base.html."""
    page = logged_in.get("/posts").text

    assert asset in page, f"страница не подключает {asset}"


@pytest.mark.parametrize("asset", ("/static/post-editor.css", "/static/post-editor.js"))
def test_the_post_page_asks_for_the_editor(logged_in, post, asset):
    page = logged_in.get(f"/posts/{post.id}").text

    assert asset in page, f"страница поста не подключает {asset}"


def test_the_login_wizard_is_asked_for_where_its_card_is_drawn():
    """Мастер входа подключается под тем же условием, что рисует его карточку.

    Без api_id и api_hash карточки нет — и файл тогда не нужен.
    """
    markup = (_TEMPLATES / "settings.html").read_text(encoding="utf-8")

    assert (
        "{% if env.telegram_api_id and env.telegram_api_hash %}\n"
        '<script src="/static/telegram-login.js"></script>\n'
        "{% endif %}"
    ) in markup


@pytest.mark.parametrize("template, gone", [
    ("base.html", ("#sidebar {", "function fmt(")),
    ("post_detail.html", ("INLINE_TAGS", "function loadDraft(")),
    ("settings.html", ("tg-state-icon'", "function showStep(")),
])
def test_the_markup_keeps_no_page_code(template, gone):
    """Разметка больше не держит в себе ни стилей, ни кода страницы."""
    markup = (_TEMPLATES / template).read_text(encoding="utf-8")

    assert "<style>" not in markup, f"{template}: стили вернулись в разметку"
    for fragment in gone:
        assert fragment not in markup, f"{template}: код вернулся в разметку ({fragment})"
