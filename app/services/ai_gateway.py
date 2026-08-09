import json
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RawPost
from app.services.prompt_settings import get_ai_system_prompt, get_ai_user_prompt_template


PROMPT_VERSION = "v1"


@dataclass
class AiResult:
    suitable: bool
    text: str
    reason: str
    model_name: str


class AiGatewayClient:
    def __init__(self):
        self.settings = get_settings()

    def _build_messages(self, raw_post: RawPost, db: Session | None = None) -> list[dict]:
        system_prompt = get_ai_system_prompt(db)
        user_template = get_ai_user_prompt_template(db)
        user_prompt = user_template.format(
            source_title=raw_post.source.title if raw_post.source else "Unknown",
            published_at_source=raw_post.published_at_source or "неизвестно",
            original_text=raw_post.original_text or "",
            has_media="да" if raw_post.has_media else "нет",
        )
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _resolve(self, db: Session | None, key: str, env_value) -> str:
        from app.services.prompt_settings import _get_setting
        return _get_setting(db, key, str(env_value) if env_value is not None else "")

    async def generate_news_post(self, raw_post: RawPost, db: Session | None = None) -> AiResult:
        base_url = self._resolve(db, "timeweb_ai_gateway_base_url", self.settings.timeweb_ai_gateway_base_url)
        api_key = self._resolve(db, "timeweb_ai_gateway_api_key", self.settings.timeweb_ai_gateway_api_key)
        model = self._resolve(db, "timeweb_ai_gateway_model", self.settings.timeweb_ai_gateway_model)
        if not base_url or not api_key:
            return AiResult(False, "", "AI gateway не настроен", model)

        try:
            temperature = float(self._resolve(db, "ai_temperature", self.settings.ai_temperature))
        except (ValueError, TypeError):
            temperature = self.settings.ai_temperature
        try:
            max_tokens = int(self._resolve(db, "ai_max_tokens", self.settings.ai_max_tokens))
        except (ValueError, TypeError):
            max_tokens = self.settings.ai_max_tokens
        try:
            timeout_seconds = int(self._resolve(db, "ai_timeout_seconds", self.settings.ai_timeout_seconds))
        except (ValueError, TypeError):
            timeout_seconds = self.settings.ai_timeout_seconds

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": self._build_messages(raw_post, db),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(base_url.rstrip("/") + "/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = self._parse_json(content)
        if parsed is None:
            return AiResult(False, "", f"AI вернул не-JSON ответ: {content[:300]}", model)
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

        # Пробуем восстановить обрезанный JSON: ищем последний полный ключ
        # Стратегия: обрезаем до последней закрытой строки и закрываем объект
        for end in range(len(stripped), 0, -1):
            try:
                candidate = stripped[:end].rstrip().rstrip(",") + "}"
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue
        return None
