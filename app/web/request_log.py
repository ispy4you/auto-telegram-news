"""Строка в логе на каждое действие оператора.

Лог контейнера раньше состоял из голых строк uvicorn: метод, путь, код.
Этого хватало, пока всё работало. Когда панель отдала пятисотку без единого
слова, выяснить по логу, что именно нажали и что сломалось, было нельзя —
трейсбек печатался отдельно и без привязки к запросу.

Поэтому пишем сами: каждое изменяющее действие (всё, кроме GET) и каждый
ответ с кодом 400 и выше — одной строкой, с адресом, признаком входа и
временем. Удачные GET не пишем: их десятки на каждую страницу, а окно лога
на хостинге — двести строк.
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.web.auth import SESSION_KEY

logger = logging.getLogger(__name__)


def who(request: Request) -> str:
    """Адрес и признак входа: «1.2.3.4 (вошёл)».

    Логин один на установку, поэтому имя пользователя ничего не добавляет —
    важно, свой это запрос или чужой, и с какого адреса.
    """
    client = request.client.host if request.client else "неизвестный адрес"
    # Сессия появляется в scope, только когда SessionMiddleware уже отработал,
    # а эта middleware стоит снаружи него: обращаемся к scope, а не к свойству,
    # которое на входе ещё падает.
    session = request.scope.get("session") or {}
    return f"{client} ({'вошёл' if session.get(SESSION_KEY) else 'гость'})"


class ActionLogMiddleware(BaseHTTPMiddleware):
    """Пишет в лог, кто что нажал и что получил в ответ."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Трейсбек и код ошибки печатает обработчик исключений в main;
            # здесь остаётся строка действия, чтобы в логе было видно, какое
            # нажатие его вызвало.
            logger.warning("%s %s от %s → ошибка", request.method, request.url.path, who(request))
            raise
        took_ms = int((time.perf_counter() - started) * 1000)
        if request.method != "GET" or response.status_code >= 400:
            write = logger.warning if response.status_code >= 400 else logger.info
            write(
                "%s %s от %s → %s за %dмс",
                request.method, request.url.path, who(request), response.status_code, took_ms,
            )
        return response
