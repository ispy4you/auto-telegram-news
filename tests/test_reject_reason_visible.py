"""Причина отказа должна быть читаема целиком, а не обрезана в карточке.

Раньше список резал её на 120 символов, а полный текст лежал только во
всплывающей подсказке: на телефоне её не вызвать, а на странице поста причины
не было вовсе.
"""

from datetime import datetime

from app.models import RawPost, RawPostStatus

LONG_REASON = (
    "Исходник — шуточная публикация развлекательного канала: в ней нет проверяемых "
    "фактов (ни даты, ни места, ни контекста высказывания), а сам текст построен на "
    "анекдоте про тест с животными. Для новостной ленты это непригодно: переписывать "
    "нечего, подтвердить нечем, а публикация как факта ввела бы читателя в заблуждение."
)


def _rejected_post(db_session, source, reason: str = LONG_REASON) -> RawPost:
    post = RawPost(
        source_id=source.id,
        telegram_message_id=226,
        original_text="Трамп похвастался, что умеет умножать числа на три.",
        status=RawPostStatus.REJECTED.value,
        ai_suitable=False,
        ai_skip_reason=reason,
        published_at_source=datetime(2026, 10, 25, 14, 14),
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


def test_the_card_carries_the_whole_reason(logged_in, db_session, source):
    _rejected_post(db_session, source)

    page = logged_in.get("/posts").text

    assert LONG_REASON in page, "в карточке причина обрезана — разворачивать нечего"
    assert "reason-more" in page, "нет кнопки «подробнее» у длинной причины"


def test_a_short_reason_gets_no_button(logged_in, db_session, source):
    _rejected_post(db_session, source, reason="Дубль вчерашнего поста.")

    page = logged_in.get("/posts").text

    assert "Дубль вчерашнего поста." in page
    assert "reason-more" not in page, "короткую причину разворачивать незачем"


def test_the_post_page_shows_the_reason_in_full(logged_in, db_session, source):
    post = _rejected_post(db_session, source)

    page = logged_in.get(f"/posts/{post.id}").text

    assert LONG_REASON in page
    assert "Отклонён AI" in page


def test_a_duplicate_says_so_instead_of_blaming_the_model(logged_in, db_session, source):
    post = _rejected_post(db_session, source, reason="Повтор поста #12, совпадение 0.97.")
    post.status = RawPostStatus.DUPLICATE.value
    db_session.commit()

    page = logged_in.get(f"/posts/{post.id}").text

    assert "Признан повтором" in page
    assert "Отклонён AI" not in page
