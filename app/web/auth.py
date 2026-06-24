import hmac

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings

SESSION_KEY = "admin_authenticated"


def require_auth(request: Request):
    settings = get_settings()
    if not settings.admin_auth_enabled:
        return True
    if not request.session.get(SESSION_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


def login_action(request: Request, username: str, password: str):
    settings = get_settings()
    if not settings.admin_auth_enabled:
        request.session[SESSION_KEY] = True
        return RedirectResponse(url="/", status_code=302)
    username_ok = hmac.compare_digest(username, settings.admin_username)
    password_ok = hmac.compare_digest(password, settings.admin_password)
    if username_ok and password_ok:
        request.session[SESSION_KEY] = True
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url="/login?error=1", status_code=302)
