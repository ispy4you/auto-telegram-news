from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from app.web.auth import require_auth

router = APIRouter()


def _svc(request: Request):
    return request.app.state.telegram_login


async def _guarded(request: Request, coro) -> JSONResponse:
    """Runs a login-service call; on invalid state transitions (RuntimeError), returns
    the current status merged with the error instead of a bare error body, so the
    frontend can always render off one consistent response shape."""
    svc = _svc(request)
    try:
        return JSONResponse(await coro)
    except RuntimeError as exc:
        return JSONResponse({**svc.status(), "error": str(exc)}, status_code=400)


@router.get("/settings/telegram-login/status")
async def telegram_login_status(request: Request, _: bool = Depends(require_auth)):
    return JSONResponse(_svc(request).status())


@router.post("/settings/telegram-login/qr/start")
async def telegram_login_qr_start(request: Request, _: bool = Depends(require_auth)):
    return await _guarded(request, _svc(request).start_qr())


@router.post("/settings/telegram-login/phone/start")
async def telegram_login_phone_start(
    request: Request, phone: str = Form(...), _: bool = Depends(require_auth)
):
    return await _guarded(request, _svc(request).start_phone(phone))


@router.post("/settings/telegram-login/code")
async def telegram_login_code(
    request: Request, code: str = Form(...), _: bool = Depends(require_auth)
):
    return await _guarded(request, _svc(request).submit_code(code))


@router.post("/settings/telegram-login/password")
async def telegram_login_password(
    request: Request, password: str = Form(...), _: bool = Depends(require_auth)
):
    return await _guarded(request, _svc(request).submit_password(password))


@router.post("/settings/telegram-login/logout")
async def telegram_login_logout(request: Request, _: bool = Depends(require_auth)):
    return JSONResponse(await _svc(request).logout())


@router.post("/settings/telegram-login/cancel")
async def telegram_login_cancel(request: Request, _: bool = Depends(require_auth)):
    return JSONResponse(await _svc(request).cancel())
