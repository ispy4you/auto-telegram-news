from app.services.text_cleanup import clean_telegram_rss_text, is_telegram_rss_garbage


def test_clean_telegram_rss_text_removes_boilerplate():
    raw = "This media is not supported in your browser VIEW IN TELEGRAM Портал в Изнанку открылся в Челябинск"
    cleaned = clean_telegram_rss_text(raw)
    assert "this media is not supported" not in cleaned.lower()
    assert "view in telegram" not in cleaned.lower()
    assert "Портал в Изнанку" in cleaned


def test_is_telegram_rss_garbage_true_for_empty_boilerplate():
    assert is_telegram_rss_garbage("This media is not supported in your browser VIEW IN TELEGRAM")


def test_is_telegram_rss_garbage_false_for_real_caption():
    text = "This media is not supported in your browser VIEW IN TELEGRAM Портал в Изнанку открылся в Челябинск"
    assert not is_telegram_rss_garbage(text)
