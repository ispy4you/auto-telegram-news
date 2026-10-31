"""Генерация поста моделью: спросить, разобрать ответ, записать черновик.

Жила в роутере страницы постов, посреди загрузки файлов и списков с
фильтрами. Здесь её можно позвать из теста и из фоновой задачи, не трогая
ни запрос, ни сессию браузера.
"""

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost
from app.services import markdown_entities, message_entities, post_lifecycle, prompts
from app.services.ai_gateway import AiGatewayClient, AiResult, GenerationFailed


async def ask_model(post: RawPost, db: Session, prompt_id: int | None = None) -> AiResult:
    """Спрашивает модель и отделяет отказ редактора от поломки.

    Техническую ошибку — недоступный шлюз, сломанный шаблон промпта — поднимаем
    наверх и пост не трогаем: раньше такая ошибка отклоняла новость навсегда,
    хотя после починки причины её достаточно сгенерировать заново.
    """
    # Без выбора промпт не подставляем: шлюз сам возьмёт основной. Так «ничего
    # не выбрали» остаётся одним состоянием, а не двумя одинаковыми на вид.
    rules = prompts.rules_for(db, prompt_id) if prompt_id else None
    result = await AiGatewayClient().generate_news_post(post, db, rules=rules)
    if result.failed:
        db.add(ActionLog(action="ai_error", entity_type="RawPost", entity_id=str(post.id), message=result.reason[:500]))
        db.commit()
        raise GenerationFailed(result.reason)

    if result.suitable and not result.text.strip():
        return replace(
            result,
            suitable=False,
            text="",
            reason="AI вернул пустой текст — возможно, обрезан лимитом токенов или промпт не дал результата",
        )
    return result


def save(post: RawPost, result: AiResult, db: Session) -> None:
    # Модель пишет markdown; Telegram такой текст показывает со звёздочками,
    # поэтому разметку разбираем сразу — её увидят и редактор, и предпросмотр.
    text, marks = markdown_entities.parse(result.text)
    db.add(GeneratedPost(
        raw_post_id=post.id,
        generated_text=text,
        generated_entities=message_entities.dumps(marks),
        model_name=result.model_name,
        status=GeneratedPostStatus.DRAFT.value if result.suitable else GeneratedPostStatus.REJECTED.value,
        generation_error=result.reason if not result.suitable else None,
    ))
    if result.suitable:
        post_lifecycle.mark_generated(post)
    else:
        post_lifecycle.reject(post)
    post.ai_suitable = result.suitable
    post.ai_skip_reason = result.reason if not result.suitable else None
    db.commit()


async def generate(post: RawPost, db: Session, prompt_id: int | None = None) -> None:
    """Спросить модель и сразу записать результат.

    Два шага врозь нужны только перегенерации: там старый черновик снимают
    между вопросом и ответом, чтобы сорвавшаяся генерация не оставила пост
    вообще без текста.
    """
    save(post, await ask_model(post, db, prompt_id), db)


async def generate_many(ids: list[int]) -> None:
    """Пачка постов в фоне: своя сессия, потому что запрос уже закрыт."""
    with SessionLocal() as db:
        posts_q = db.scalars(select(RawPost).where(RawPost.id.in_(ids))).all()
        for p in posts_q:
            try:
                await generate(p, db)
            except GenerationFailed:
                # Причина одна на всю пачку — она уже в журнале как ai_error,
                # а остальные посты незачем прогонять по тем же граблям.
                break
            except Exception as exc:
                db.add(ActionLog(action="bulk_generate_error", entity_type="RawPost", entity_id=str(p.id), message=str(exc)))
                db.commit()


async def generate_by_id(post_id: int, db: Session, prompt_id: int | None = None) -> None:
    """То же, но пост ещё нужно достать: роут знает только его номер."""
    post = db.get(RawPost, post_id)
    if not post:
        return
    await generate(post, db, prompt_id)
