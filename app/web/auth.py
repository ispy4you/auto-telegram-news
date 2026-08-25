import hmac
import time

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings

SESSION_KEY = "admin_authenticated"

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 15 * 60
_LOCKOUT_SECONDS = 15 * 60

# In-memory per-IP failure tracker: {ip: [timestamps of recent failed attempts]}.
# Fine for a single-process MVP deployment; would need a shared store (e.g. Redis)
# behind multiple workers/instances.
_failed_attempts: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_locked_out(ip: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < _WINDOW_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) >= _MAX_ATTEMPTS and (now - attempts[-_MAX_ATTEMPTS]) < _LOCKOUT_SECONDS


def _record_failure(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(time.monotonic())


def _clear_failures(ip: str) -> None:
    _failed_attempts.pop(ip, None)


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

    ip = _client_ip(request)
    if _is_locked_out(ip):
        return RedirectResponse(url="/login?error=locked", status_code=302)

    # Сравниваем байты: compare_digest на строках падает с TypeError, если в них
    # есть не-ASCII символы, то есть кириллический пароль ронял вход пятисоткой.
    username_ok = hmac.compare_digest(username.encode("utf-8"), settings.admin_username.encode("utf-8"))
    password_ok = hmac.compare_digest(password.encode("utf-8"), settings.admin_password.encode("utf-8"))
    if username_ok and password_ok:
        _clear_failures(ip)
        request.session[SESSION_KEY] = True
        return RedirectResponse(url="/", status_code=302)

    _record_failure(ip)
    return RedirectResponse(url="/login?error=1", status_code=302)
