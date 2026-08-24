from app.services.ai_gateway import AiGatewayClient


def test_parse_json_plain():
    result = AiGatewayClient._parse_json('{"suitable": true, "text": "hello", "reason": ""}')
    assert result == {"suitable": True, "text": "hello", "reason": ""}


def test_parse_json_strips_markdown_fence():
    content = '```json\n{"suitable": true, "text": "hello"}\n```'
    result = AiGatewayClient._parse_json(content)
    assert result == {"suitable": True, "text": "hello"}


def test_parse_json_recovers_truncated_object():
    # Simulates a response cut off mid-string by a token limit.
    content = '{"suitable": true, "text": "hello world", "reason": "cut off her'
    result = AiGatewayClient._parse_json(content)
    assert result is not None
    assert result["suitable"] is True
    assert result["text"] == "hello world"


def test_parse_json_gives_up_when_nothing_is_complete():
    # Обрезано до первой полной пары — восстанавливать нечего.
    assert AiGatewayClient._parse_json('{"text": "начало обрез') is None


def test_parse_json_ignores_commas_inside_strings_and_nested_objects():
    content = '{"suitable": true, "meta": {"a": 1, "b": 2}, "text": "раз, два, три", "reason": "обре'
    result = AiGatewayClient._parse_json(content)
    assert result == {"suitable": True, "meta": {"a": 1, "b": 2}, "text": "раз, два, три"}


def test_parse_json_returns_none_for_garbage():
    assert AiGatewayClient._parse_json("not json at all") is None


def test_missing_configuration_is_a_technical_failure(db_session):
    """Ненастроенный шлюз — не повод отклонить новость навсегда."""
    import asyncio

    result = asyncio.run(AiGatewayClient().generate_news_post(object(), db_session))

    assert result.failed is True
    assert result.suitable is False
