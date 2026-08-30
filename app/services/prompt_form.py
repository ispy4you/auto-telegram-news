"""Анкета: правила для AI, собранные из переключателей.

Свободный текст правил честнее, но требует понимать, что вообще бывает
написано в промпте. Анкета отвечает на этот вопрос списком: человек кликает
и видит, во что превратился его клик — из тех же строк, что склеивает compose.

Ответы хранятся отдельным ключом, но результат всё равно уезжает в ai_prompt:
генерация про анкету не знает и знать не должна.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: Всегда первым абзацем: без этого промпт начинается с середины.
INTRO = "Ты редактор новостного Telegram-канала. Перепиши исходную новость в пост на русском языке."

#: Всегда последним: дисциплина по фактам не обсуждается переключателем.
OUTRO = (
    "Используй только факты из исходного текста. Не выдумывай факты, цифры, имена, "
    "причины и последствия.\n"
    "Если фактов не хватает на самостоятельный пост — откажись от новости и коротко "
    "объясни почему."
)

#: Сколько символов свободного дополнения имеет смысл принимать.
MAX_EXTRA = 2000


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    #: Строка, которая уйдёт в промпт. Пустая — вариант ничего не добавляет.
    line: str


@dataclass(frozen=True)
class Question:
    key: str
    label: str
    options: tuple[Option, ...]

    @property
    def default(self) -> str:
        return self.options[0].value

    def line(self, value: str) -> str:
        for option in self.options:
            if option.value == value:
                return option.line
        return self.options[0].line


QUESTIONS: tuple[Question, ...] = (
    Question("tone", "Тон", (
        Option("neutral", "Нейтральный", "Пиши нейтрально, в обычном новостном стиле, без оценок."),
        Option("dry", "Сухие новости", "Пиши сухо и телеграфно: только факты, никаких вводных слов."),
        Option("lively", "Живой разговорный", "Пиши живо и просто, как рассказывают знакомым, но без панибратства."),
        Option("expert", "Экспертный разбор", "Пиши как эксперт: коротко объясняй, что происходящее значит, не выходя за факты исходника."),
    )),
    Question("length", "Длина", (
        Option("medium", "Средне", "Держись 700–1200 знаков."),
        Option("short", "Коротко", "Держись 400–700 знаков, только суть."),
        Option("full", "Сколько нужно", "Пиши столько, сколько нужно, чтобы изложить все факты из исходника."),
    )),
    Question("emoji", "Эмодзи", (
        Option("none", "Нет", "Не используй эмодзи."),
        Option("some", "Умеренно", "Можно один-два уместных эмодзи, не больше."),
        Option("free", "Свободно", "Эмодзи используй свободно, если они к месту."),
    )),
    Question("hashtags", "Хештеги", (
        Option("none", "Нет", "Не добавляй хештеги."),
        Option("few", "2–3 в конце", "В конце добавь два-три хештега по теме новости."),
    )),
    Question("headline", "Заголовок первой строкой", (
        Option("no", "Не нужен", "Отдельный заголовок первой строкой не нужен."),
        Option("yes", "Нужен", "Начни с короткого заголовка отдельной первой строкой."),
    )),
    Question("source_link", "Ссылка на источник", (
        Option("no", "Не добавлять", "Не добавляй ссылку на источник."),
        Option("yes", "В конце", "В конце поста добавь ссылку на источник."),
    )),
    Question("ads", "Реклама и промо", (
        Option("reject", "Отклонять", "Если исходник рекламный или это анонс продажи — откажись от новости."),
        Option("rewrite", "Переписывать нейтрально", "Если исходник рекламный, перескажи его нейтрально, без призывов и обещаний."),
    )),
    Question("language", "Язык", (
        Option("ru", "Русский", "Пиши на русском языке."),
        Option("source", "Как в исходнике", "Пиши на том же языке, что и исходная новость."),
    )),
)

BY_KEY = {question.key: question for question in QUESTIONS}


def defaults() -> dict[str, str]:
    answers = {question.key: question.default for question in QUESTIONS}
    answers["extra"] = ""
    return answers


def normalize(answers: dict[str, str]) -> dict[str, str]:
    """Оставляет только известные вопросы и известные ответы."""
    clean = defaults()
    for key, value in (answers or {}).items():
        if key == "extra":
            clean["extra"] = str(value or "").strip()[:MAX_EXTRA]
        elif key in BY_KEY and any(option.value == value for option in BY_KEY[key].options):
            clean[key] = value
    return clean


def compose(answers: dict[str, str]) -> str:
    """Текст правил из ответов анкеты."""
    clean = normalize(answers)
    lines = [line for line in (question.line(clean[question.key]) for question in QUESTIONS) if line]
    parts = [INTRO, "\n".join(lines), OUTRO]
    if clean["extra"]:
        parts.append(clean["extra"])
    return "\n\n".join(parts)


def loads(raw: str | None) -> dict[str, str]:
    """Ответы из хранимого JSON. Мусор в базе не должен ронять страницу."""
    try:
        stored = json.loads(raw or "{}")
    except (TypeError, ValueError):
        stored = {}
    return normalize(stored if isinstance(stored, dict) else {})


def dumps(answers: dict[str, str]) -> str:
    return json.dumps(normalize(answers), ensure_ascii=False)


def lines_for_browser() -> dict[str, dict[str, str]]:
    """Те же строки для живого предпросмотра в форме.

    Склейку браузер повторяет, а тексты берёт отсюда: иначе предпросмотр и
    сохранённый промпт разъедутся на первой же правке формулировки.
    """
    return {question.key: {option.value: option.line for option in question.options}
            for question in QUESTIONS}
