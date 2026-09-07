"""Список правил генерации: создать, поправить, назначить основным.

Раньше правила были одним полем в настройках. Здесь их несколько, и у страницы
две задачи: показать, какой промпт сейчас основной, и дать проверить правку на
живой новости до того, как ею начнут генерировать посты.
"""

import urllib.parse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog, Prompt
from app.services import ai_prompt, prompt_template, prompts as prompts_service
from app.web.auth import require_auth
from app.web.routes.common import tpl

router = APIRouter()

NAME_MAX = 120
BODY_MAX = 20000


def _list_url(ok: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {k: v for k, v in (("ok", ok), ("error", error)) if v}
    url = "/prompts" + ("?" + urllib.parse.urlencode(params) if params else "")
    return RedirectResponse(url=url, status_code=302)


def _clean_name(value: str, fallback: str) -> str:
    return (value or "").strip()[:NAME_MAX] or fallback


@router.get("/prompts")
async def prompts_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    return tpl(request, "prompts.html", db, {
        "prompts": prompts_service.all_prompts(db),
        "ok": request.query_params.get("ok"),
        "error": request.query_params.get("error"),
    })


@router.get("/prompts/{prompt_id}")
async def prompt_editor(
    prompt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        return _list_url(error="Промпт не найден.")
    return tpl(request, "prompt_edit.html", db, {
        "prompt": prompt,
        "auto_appendix": ai_prompt.appendix_example(),
        "response_contract": ai_prompt.RESPONSE_CONTRACT,
        "warnings": request.query_params.getlist("warn"),
        "ok": request.query_params.get("ok"),
    })


@router.post("/prompts")
async def create_prompt(
    name: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """Новый промпт заводится с текстом действующего основного.

    Пустое поле правил — плохая отправная точка: чаще всего нужен тот же тон с
    парой отличий, а не чистый лист.
    """
    prompt = Prompt(
        name=_clean_name(name, "Без названия"),
        body=prompts_service.rules_for(db),
        is_default=False,
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return RedirectResponse(url=f"/prompts/{prompt.id}", status_code=302)


@router.post("/prompts/{prompt_id}")
async def save_prompt(
    prompt_id: int,
    name: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        return _list_url(error="Промпт не найден.")

    prompt.name = _clean_name(name, prompt.name)
    prompt.body = (body or "")[:BODY_MAX]
    db.commit()

    # Те же предупреждения, что раньше показывались при сохранении настроек:
    # опечатка в плейсхолдере молча превращала бы его в текст промпта.
    warnings = prompt_template.problems(prompt.body)
    params = [("ok", "Сохранено")] + [("warn", w) for w in warnings]
    return RedirectResponse(
        url=f"/prompts/{prompt_id}?" + urllib.parse.urlencode(params),
        status_code=302,
    )


@router.post("/prompts/{prompt_id}/default")
async def make_default(prompt_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        return _list_url(error="Промпт не найден.")
    prompts_service.set_default(db, prompt)
    db.add(ActionLog(
        action="prompt_default",
        entity_type="Prompt",
        entity_id=str(prompt.id),
        message=f"Основной промпт: «{prompt.name}»",
    ))
    db.commit()
    return _list_url(ok=f"«{prompt.name}» теперь основной.")


@router.post("/prompts/{prompt_id}/delete")
async def delete_prompt(prompt_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        return _list_url(error="Промпт не найден.")

    others = sorted(
        (p for p in prompts_service.all_prompts(db) if p.id != prompt.id),
        key=lambda p: p.id,
    )
    if not others:
        # Генерации нужны хоть какие-то правила. Пустой список — это молчаливый
        # откат к константе из кода, чего оператор не увидит и не поймёт.
        return _list_url(error="Это последний промпт — сначала заведите другой.")

    was_default = prompt.is_default
    name = prompt.name
    db.delete(prompt)
    db.flush()
    if was_default:
        prompts_service.set_default(db, others[0])
    db.commit()

    if was_default:
        return _list_url(ok=f"«{name}» удалён, основным стал «{others[0].name}».")
    return _list_url(ok=f"«{name}» удалён.")
