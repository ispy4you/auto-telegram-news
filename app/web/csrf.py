import hmac
import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_EXEMPT_PATHS = {"/login"}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit session token CSRF protection.

    Reads form body (Starlette caches it in request._body so route handlers
    can still read it), extracts csrf_token field, and compares to the value
    stored in the server-side session using a constant-time comparison.
    """

    async def dispatch(self, request: Request, call_next):
        # Ensure every session has a token (set on first GET, checked on POST)
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_hex(32)

        if request.method == "POST" and request.url.path not in _EXEMPT_PATHS:
            session_token = request.session.get("csrf_token", "")
            body = await request.body()  # Starlette caches in _body; routes can still re-read
            form_token = ""
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type:
                try:
                    parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
                    form_token = (parsed.get("csrf_token") or [""])[0]
                except Exception:
                    pass
            if not session_token or not hmac.compare_digest(form_token, session_token):
                return Response("Forbidden: недействительный CSRF-токен", status_code=403)

        return await call_next(request)
