"""Вкладка «Генерация»: текст приносит редактор, а не сборщик каналов.

Иногда новость приходит мимо источников — из мессенджера, с сайта, из головы.
Прогонять её через те же правила, что и всё остальное, до сих пор было негде.
Здесь текст вставляют руками, а дальше он становится обычным постом: та же
модель, тот же промпт из настроек, та же страница поста с редактором.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog
from app.services import manual_post
from app.services.ai_gateway import AiGatewayClient
from app.web.auth import require_auth
from app.web.routes.common import current_project_id, tpl

router = APIRouter()

#: Ниже этого текст на новость не тянет — незачем тратить на него запрос.
MIN_TEXT_LEN = 10
#: Верхняя граница на случай, когда в поле улетела целая статья или файл
#: целиком: длинный запрос стоит денег и всё равно упрётся в контекст модели.
MAX_TEXT_LEN = 20000


def _page(request: Request, db: Session, text: str = "", error: str | None = None, refusal: str | None = None):
    return tpl(request, "compose.html", db, {
        "text": text, "error": error, "refusal": refusal,
        "manual_source_title": manual_post.SOURCE_TITLE,
    })


@router.get("/compose")
def compose_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    return _page(request, db)


@router.post("/compose")
async def compose_generate(
    request: Request,
    source_text: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    text = (source_text or "").strip()
    if len(text) < MIN_TEXT_LEN:
        return _page(request, db, text, error=f"Слишком короткий текст: нужно хотя бы {MIN_TEXT_LEN} символов.")
    if len(text) > MAX_TEXT_LEN:
        return _page(request, db, text, error=(
            f"Слишком длинный текст: {len(text)} символов при пределе {MAX_TEXT_LEN}. "
            f"Оставьте саму новость, без всего остального."
        ))

    result = await AiGatewayClient().generate(manual_post.prompt_values(text), db)

    # Текст всегда возвращается в поле: он набран руками, и терять его при
    # любой осечке — худшее, что может сделать эта страница.
    if result.failed:
        db.add(ActionLog(action="ai_error", entity_type="Compose", entity_id="-", message=result.reason[:500]))
        db.commit()
        return _page(request, db, text, error=result.reason)

    if not result.suitable or not result.text.strip():
        reason = result.reason.strip() or "Модель не вернула текст поста."
        return _page(request, db, text, refusal=reason)

    post = manual_post.create(
        db,
        current_project_id(request, db),
        original_text=text,
        generated_text=result.text,
        model_name=result.model_name,
    )
    db.add(ActionLog(
        action="manual_compose",
        entity_type="RawPost",
        entity_id=str(post.id),
        message="Пост собран из текста, вставленного вручную",
    ))
    db.commit()
    return RedirectResponse(url=f"/posts/{post.id}", status_code=302)
