from app.services.deduplication import DeduplicationService


def test_normalize_text_removes_links_and_symbols():
    service = DeduplicationService()
    normalized = service.normalize_text("Привет!!! https://example.com TEST")
    assert normalized == "привет test"


def test_text_hash_is_deterministic():
    service = DeduplicationService()
    text = "один и тот же"
    assert service.text_hash(text) == service.text_hash(text)
