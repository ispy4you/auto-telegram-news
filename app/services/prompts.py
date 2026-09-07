"""Список правил генерации и выбор действующих.

Промпт перестал быть одной настройкой: у канала про здоровье и у новостной
ленты разный тон, и держать это в одном тексте не получалось — его правили
перед генерацией и возвращали обратно.

Ровно один промпт помечен основным. Это не украшение списка, а ответ на вопрос
«с чем генерировать, если ничего не выбрали», который задаётся при каждой
автопубликации.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Prompt
from app.services.ai_prompt import DEFAULT_AI_PROMPT

FALLBACK_NAME = "Основной"


def all_prompts(db: Session) -> list[Prompt]:
    """Основной первым, остальные по имени — так его видно без чтения подписей."""
    return list(db.scalars(select(Prompt).order_by(Prompt.is_default.desc(), Prompt.name)).all())


def default_prompt(db: Session) -> Prompt | None:
    prompt = db.scalar(select(Prompt).where(Prompt.is_default.is_(True)).order_by(Prompt.id))
    if prompt is not None:
        return prompt
    # Пометку могли снять руками в базе. Пустой список правил хуже любого
    # выбора, поэтому берём самый старый — он и был основным до всех правок.
    return db.scalar(select(Prompt).order_by(Prompt.id))


def set_default(db: Session, prompt: Prompt) -> None:
    for other in db.scalars(select(Prompt).where(Prompt.is_default.is_(True))).all():
        other.is_default = False
    prompt.is_default = True


def rules_for(db: Session, prompt_id: int | None = None) -> str:
    """Текст правил: выбранный промпт, иначе основной, иначе константа из кода.

    Константа — не «на всякий случай»: таблица пуста, пока не отработала
    миграция, и в этот момент генерация всё равно должна работать.
    """
    prompt = db.get(Prompt, prompt_id) if prompt_id else None
    if prompt is None:
        prompt = default_prompt(db)
    return prompt.body if prompt is not None else DEFAULT_AI_PROMPT


def rules(db: Session | None = None, prompt_id: int | None = None) -> str:
    """То же самое, но переживает вызов без сессии — как настройки до этого."""
    if db is not None:
        return rules_for(db, prompt_id)
    with SessionLocal() as own:
        return rules_for(own, prompt_id)
