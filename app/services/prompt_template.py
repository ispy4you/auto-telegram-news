"""Подстановка данных поста в пользовательский шаблон промпта.

Раньше здесь был `str.format()`, и любая фигурная скобка в тексте считалась
плейсхолдером: строка `Верни {"suitable": true}` роняла генерацию с KeyError
ещё до запроса к шлюзу, а пользователь видел 500. Причём промпт по умолчанию
сам требовал писать `{{` вместо `{`, и попытка «исправить опечатку» в форме
настроек ломала генерацию.

Поэтому подставляем только известные имена, а остальной текст — включая любые
скобки и JSON-примеры — уходит в модель как есть.
"""

import re

#: Что шаблон умеет подставлять. Порядок — для подсказки в форме настроек.
PLACEHOLDERS = ("source_title", "published_at_source", "original_text", "has_media")

#: `{имя}` — только латиница, цифры и подчёркивание: всё прочее не плейсхолдер.
_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Экранирование, которого требовал format(). В шаблонах, написанных до отказа
#: от него, `{{` означало одну скобку.
_LEGACY_ESCAPE = re.compile(r"\{\{|\}\}")


def render(template: str, values: dict[str, str]) -> str:
    """Заменяет известные плейсхолдеры; текст вокруг них не трогает."""
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name in PLACEHOLDERS:
            return str(values.get(name, ""))
        return match.group(0)

    return _TOKEN.sub(substitute, template)


def unknown_placeholders(template: str) -> list[str]:
    """Имена вида `{что_то}`, которые подставить нечем.

    Это почти всегда опечатка в имени настоящего плейсхолдера: `{text}` вместо
    `{original_text}`. Сломать генерацию такое больше не может, но и данных
    в промпт не принесёт, поэтому о нём стоит сказать вслух.
    """
    seen: list[str] = []
    for match in _TOKEN.finditer(template):
        name = match.group(1)
        if name not in PLACEHOLDERS and name not in seen:
            seen.append(name)
    return seen


def problems(template: str) -> list[str]:
    """Человеческие предупреждения по шаблону. Пустой список — вопросов нет."""
    issues: list[str] = []
    if not template.strip():
        issues.append("Шаблон промпта пустой — модель не получит ни текста новости, ни задания.")
        return issues
    if "{original_text}" not in template:
        issues.append(
            "В шаблоне нет {original_text} — модель не увидит текст новости "
            "и будет придумывать пост с нуля."
        )
    unknown = unknown_placeholders(template)
    if unknown:
        names = ", ".join("{%s}" % name for name in unknown)
        issues.append(
            f"Непонятные плейсхолдеры: {names}. Они уйдут в промпт как обычный текст. "
            f"Доступны: " + ", ".join("{%s}" % name for name in PLACEHOLDERS) + "."
        )
    return issues


def unescape_legacy(template: str) -> str:
    """`{{` → `{` для шаблонов, написанных во времена format()."""
    return _LEGACY_ESCAPE.sub(lambda m: m.group(0)[0], template)
