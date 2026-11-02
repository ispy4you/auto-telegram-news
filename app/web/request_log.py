"""Строка в логе на каждое действие оператора.

Лог контейнера раньше состоял из голых строк uvicorn: метод, путь, код.
Этого хватало, пока всё работало. Когда панель отдала пятисотку без единого
слова, выяснить по логу, что именно нажали и что сломалось, было нельзя —
трейсбек печатался отдельно и без привязки к запросу.

Поэтому пишем сами: каждое изменяющее действие (всё, кроме GET) и каждый
ответ с кодом 400 и выше — одной строкой, с адресом, признаком входа и
временем. Удачные GET не пишем: их десятки на каждую страницу, а окно лога
на хостинге — двести строк.

Те же изменяющие действия ложатся в журнал панели, в таблицу admin_logs:
лог контейнера живёт до перезапуска и показывает последние двести строк,
а на вопрос «кто и когда это поменял» нужна история за недели.
"""

import asyncio
import logging
import time

from starlette.requests import Request

from app.database import SessionLocal
from app.web import admin_journal
from app.web.auth import SESSION_KEY

logger = logging.getLogger(__name__)

#: Фабрика сессий для журнала. Своя, а не зависимость запроса: запрос к этому
#: моменту уже завершён, а его сессия закрыта. Тесты её подменяют.
journal_session = SessionLocal


def _ip(request: Request) -> str:
    return request.client.host if request.client else "неизвестный адрес"


def _authenticated(request: Request) -> bool:
    # Сессия появляется в scope, только когда SessionMiddleware уже отработал,
    # а эта middleware стоит снаружи него: обращаемся к scope, а не к свойству,
    # которое на входе ещё падает.
    session = request.scope.get("session") or {}
    return bool(session.get(SESSION_KEY))


def _header(message, name: bytes) -> str:
    """Заголовок ответа по имени. Нужен один — куда увела форма входа."""
    for key, value in message.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def who(request: Request) -> str:
    """Адрес и признак входа: «1.2.3.4 (вошёл)».

    Логин один на установку, поэтому имя пользователя ничего не добавляет —
    важно, свой это запрос или чужой, и с какого адреса.
    """
    return f"{_ip(request)} ({'вошёл' if _authenticated(request) else 'гость'})"


class ActionLogMiddleware:
    """Пишет в лог, кто что нажал и что получил в ответ.

    Обычная ASGI-middleware, а не BaseHTTPMiddleware: она стоит на каждом
    запросе, а панель отдаёт медиафайлы — до пятидесяти мегабайт. Базовый
    класс прогонял бы тело каждого такого ответа через промежуточный поток,
    хотя нужен от ответа только его код.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        started = time.perf_counter()
        status = 0
        location = ""

        async def capture(message):
            nonlocal status, location
            if message["type"] == "http.response.start":
                status = message["status"]
                location = _header(message, b"location")
            await send(message)

        try:
            await self.app(scope, receive, capture)
        except Exception:
            # Трейсбек и код ошибки печатает обработчик исключений в main;
            # здесь остаётся строка действия, чтобы в логе было видно, какое
            # нажатие его вызвало.
            logger.warning("%s %s от %s → ошибка", request.method, request.url.path, who(request))
            await self._journal(request, 500, "")
            raise

        took_ms = int((time.perf_counter() - started) * 1000)
        if request.method != "GET" or status >= 400:
            write = logger.warning if status >= 400 else logger.info
            write(
                "%s %s от %s → %s за %dмс",
                request.method, request.url.path, who(request), status, took_ms,
            )
        if request.method != "GET":
            await self._journal(request, status, location)

    @staticmethod
    async def _journal(request: Request, status: int, location: str) -> None:
        """Запись в журнал — в отдельном потоке: вставка в базу синхронная.

        В цикле событий она задержала бы все остальные запросы, а медленная
        или далёкая база — на всё время своего ответа.
        """
        await asyncio.to_thread(
            admin_journal.record,
            journal_session,
            method=request.method,
            path=request.url.path,
            actor=_ip(request),
            authenticated=_authenticated(request),
            status=status,
            location=location,
        )
