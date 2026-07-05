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


def test_parse_json_returns_none_for_garbage():
    assert AiGatewayClient._parse_json("not json at all") is None
