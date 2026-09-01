import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.models import RawPost
from app.services import ai_prompt, settings_registry
from app.services.prompt_settings import get_ai_prompt


logger = logging.getLogger(__name__)

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
    #: Почему модель остановилась: пригодилось проверке промпта в настройках.
    finish_reason: str = ""


class AiGatewayClient:
    @staticmethod
    def _post_values(raw_post: RawPost) -> dict[str, str]:
        return {
            "source_title": raw_post.source.title if raw_post.source else "Unknown",
            "published_at_source": raw_post.published_at_source or "неизвестно",
            "original_text": raw_post.original_text or "",
            "has_media": "да" if raw_post.has_media else "нет",
        }

    def _build_messages(
        self,
        raw_post: RawPost,
        db: Session | None = None,
        rules: str | None = None,
    ) -> list[dict]:
        return self._messages_from_values(self._post_values(raw_post), db, rules)

    def _messages_from_values(
        self,
        values: dict[str, str],
        db: Session | None = None,
        rules: str | None = None,
    ) -> list[dict]:
        """rules задаётся только проверкой промпта из настроек: там текст берут
        прямо из поля, не сохраняя его."""
        user_prompt = ai_prompt.build_user_message(
            get_ai_prompt(db) if rules is None else rules,
            values,
        )
        return [
            {"role": "system", "content": ai_prompt.RESPONSE_CONTRACT},
            {"role": "user", "content": user_prompt},
        ]

    async def generate_news_post(
        self,
        raw_post: RawPost,
        db: Session | None = None,
        rules: str | None = None,
    ) -> AiResult:
        """Новость из канала: данные для промпта берутся из самого поста."""
        try:
            values = self._post_values(raw_post)
        except Exception as exc:
            model = settings_registry.get("timeweb_ai_gateway_model", db)
            return AiResult(False, "", f"Не удалось собрать промпт: {type(exc).__name__}: {exc}", model, failed=True)
        return await self.generate(values, db, rules)

    async def generate(
        self,
        values: dict[str, str],
        db: Session | None = None,
        rules: str | None = None,
    ) -> AiResult:
        """Генерация по готовым данным новости — источник их не важен.

        Ручной ввод из панели приходит сюда напрямую: поста в базе ещё нет, а
        промпт и разбор ответа нужны ровно те же, что и для новости из канала.
        """
        base_url = settings_registry.get("timeweb_ai_gateway_base_url", db)
        api_key = settings_registry.get("timeweb_ai_gateway_api_key", db)
        model = settings_registry.get("timeweb_ai_gateway_model", db)
        if not base_url or not api_key:
            return AiResult(False, "", "AI gateway не настроен", model, failed=True)

        temperature = settings_registry.get("ai_temperature", db)
        max_tokens = settings_registry.get("ai_max_tokens", db)
        timeout_seconds = settings_registry.get("ai_timeout_seconds", db)

        try:
            messages = self._messages_from_values(values, db, rules)
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

        choice = (data.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish = choice.get("finish_reason") or "не указан"

        if not content.strip():
            # Пустой ответ раньше доходил до панели как «AI вернул не-JSON ответ:»
            # с пустотой после двоеточия — устранять было нечего.
            logger.warning(
                "Пустой ответ шлюза: model=%s finish_reason=%s usage=%s поля ответа=%s",
                model, finish, data.get("usage"), sorted(message),
            )
            return AiResult(False, "", self._describe_empty(finish, message, max_tokens), model, failed=True, finish_reason=finish)

        parsed = self._parse_json(content)
        if parsed is None:
            logger.warning("Неразбираемый ответ шлюза: model=%s finish_reason=%s", model, finish)
            return AiResult(
                False, "",
                f"AI вернул не-JSON ответ (finish_reason={finish}): {content[:300]}",
                model, failed=True, finish_reason=finish,
            )
        return AiResult(
            bool(parsed.get("suitable")), parsed.get("text", ""), parsed.get("reason", ""),
            model, finish_reason=finish,
        )

    @staticmethod
    def _describe_empty(finish: str, message: dict, max_tokens: int) -> str:
        """Почему ответ пустой — словами, которые говорят, что делать."""
        thinking = (message.get("reasoning_content") or message.get("reasoning") or "").strip()

        if finish == "length" or thinking:
            spent = " Модель потратила лимит на рассуждение и не дошла до ответа." if thinking else ""
            return (
                f"Модель упёрлась в лимит ответа: ai_max_tokens = {max_tokens}.{spent} "
                f"Увеличьте лимит в настройках или возьмите модель попроще."
            )
        if finish == "content_filter":
            return "Модель отказалась отвечать: сработал её собственный фильтр содержимого."
        return (
            f"Модель вернула пустой ответ (finish_reason={finish}). "
            f"Обычно так отвечает модель, не поддерживающая ответ строго в JSON — "
            f"проверьте, та ли модель указана в настройках."
        )

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
