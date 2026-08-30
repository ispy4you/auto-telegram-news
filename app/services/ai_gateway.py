import json
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.models import RawPost
from app.services import prompt_template, settings_registry
from app.services.prompt_settings import get_ai_system_prompt, get_ai_user_prompt_template


PROMPT_VERSION = "v1"


class GenerationFailed(RuntimeError):
    """Генерация сорвалась по технической причине: шлюз, сеть или сам промпт.

    Отличается от «новость не подходит» тем, что пост остаётся нетронутым:
    причину можно устранить и попробовать снова.
    """


@dataclass
class AiResult:
    suitable: bool
    text: str
    reason: str
    model_name: str
    # True — шлюз не ответил или ответил мусором. Это не «новость не подходит»:
    # пост нужно не отклонять, а обработать заново на следующем прогоне.
    failed: bool = False


class AiGatewayClient:
    def _build_messages(self, raw_post: RawPost, db: Session | None = None) -> list[dict]:
        system_prompt = get_ai_system_prompt(db)
        user_template = get_ai_user_prompt_template(db)
        user_prompt = prompt_template.render(user_template, {
            "source_title": raw_post.source.title if raw_post.source else "Unknown",
            "published_at_source": raw_post.published_at_source or "неизвестно",
            "original_text": raw_post.original_text or "",
            "has_media": "да" if raw_post.has_media else "нет",
        })
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    async def generate_news_post(self, raw_post: RawPost, db: Session | None = None) -> AiResult:
        base_url = settings_registry.get("timeweb_ai_gateway_base_url", db)
        api_key = settings_registry.get("timeweb_ai_gateway_api_key", db)
        model = settings_registry.get("timeweb_ai_gateway_model", db)
        if not base_url or not api_key:
            return AiResult(False, "", "AI gateway не настроен", model, failed=True)

        temperature = settings_registry.get("ai_temperature", db)
        max_tokens = settings_registry.get("ai_max_tokens", db)
        timeout_seconds = settings_registry.get("ai_timeout_seconds", db)

        try:
            messages = self._build_messages(raw_post, db)
        except Exception as exc:
            return AiResult(False, "", f"Не удалось собрать промпт: {type(exc).__name__}: {exc}", model, failed=True)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(base_url.rstrip("/") + "/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return AiResult(False, "", f"AI gateway недоступен: {type(exc).__name__}: {exc}", model, failed=True)

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = self._parse_json(content)
        if parsed is None:
            return AiResult(False, "", f"AI вернул не-JSON ответ: {content[:300]}", model, failed=True)
        return AiResult(bool(parsed.get("suitable")), parsed.get("text", ""), parsed.get("reason", ""), model)

    @staticmethod
    def _parse_json(content: str) -> dict | None:
        # Убираем markdown-обёртку ```json ... ```
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Ответ обрезан лимитом токенов. Отбрасываем незавершённую пару
        # ключ-значение и закрываем объект — один разбор вместо перебора всех
        # длин строки, который на ответе в 10 КБ давал десять тысяч попыток.
        cut = AiGatewayClient._last_top_level_comma(stripped)
        if cut is None:
            return None
        try:
            result = json.loads(stripped[:cut] + "}")
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _last_top_level_comma(text: str) -> int | None:
        """Позиция последней запятой верхнего уровня вне строкового литерала."""
        depth = 0
        in_string = False
        escaped = False
        last_comma: int | None = None
        for index, char in enumerate(text):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = not in_string
            elif not in_string:
                if char in "{[":
                    depth += 1
                elif char in "}]":
                    depth -= 1
                elif char == "," and depth == 1:
                    last_comma = index
        return last_comma
