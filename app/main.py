import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus
from app.migrations import run_migrations
from app.services import telegram_session_store
from app.services.scheduler import SchedulerService
from app.services.telegram_event_listener import TelegramEventListenerService
from app.services.telegram_login import TelegramLoginService
from app.web.csrf import CSRFMiddleware
from app.web.request_log import ActionLogMiddleware, who
from app.web.routes import router
from app.web.routes.common import templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Библиотеки логируют довольно шумно на INFO — приглушаем, оставляя WARNING+.
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


#: Служебные запросы, которые идут сами по себе, без участия человека.
_NOISY_PATHS = ("/health", "/api/scheduler-status")


class _SkipNoisyAccessLog(logging.Filter):
    """Хостинг отдаёт только последние 200 строк лога.

    Сначала все 200 были про /health, который хостинг дёргает каждые несколько
    секунд. Когда его убрали, их место занял /api/scheduler-status: его опрашивает
    каждая открытая вкладка панели. Настоящие действия и ошибки в таком логе
    не найти.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Путь сравниваем целиком, с границей после него: иначе под фильтр
        # попадёт любой адрес, который просто начинается так же.
        message = record.getMessage()
        return not any(f" {path} " in message or f" {path}?" in message for path in _NOISY_PATHS)


logging.getLogger("uvicorn.access").addFilter(_SkipNoisyAccessLog())

logger = logging.getLogger(__name__)

settings = get_settings()


def _bootstrap() -> None:
    """Подготовка окружения: схема БД, каталоги, обязательные записи.

    Всё это выполняется при запуске, а не при импорте модуля: импорт должен
    оставаться безопасным без живой базы — иначе приложение нельзя ни собрать
    в тестах, ни поднять с временно недоступной БД.
    """
    settings.media_root.mkdir(parents=True, exist_ok=True)
    run_migrations()

    # Установки, логинившиеся до переезда сессии в БД, переносим автоматически.
    telegram_session_store.migrate_legacy_file(settings.telegram_session_path)

    _report_stuck_posts()


def _report_stuck_posts() -> None:
    """Раньше упавшие посты молча сбрасывались в рабочий статус при каждом старте.

    Это стирало след проблемы и ничего не чинило: перезапуск не делает
    недоступный канал доступным. Теперь о них сообщается, а решение —
    за оператором: у постов есть повторная генерация и ручная публикация.
    """
    with SessionLocal() as db:
        raw_failed = db.scalar(
            select(func.count()).select_from(RawPost).where(RawPost.status == RawPostStatus.FAILED.value)
        ) or 0
        generated_failed = db.scalar(
            select(func.count()).select_from(GeneratedPost)
            .where(GeneratedPost.status == GeneratedPostStatus.FAILED.value)
        ) or 0
    if raw_failed or generated_failed:
        logger.warning(
            "Постов в статусе FAILED: %s исходных, %s сгенерированных — разберитесь в панели",
            raw_failed,
            generated_failed,
        )


event_listener = TelegramEventListenerService()
scheduler_service = SchedulerService(
    interval_seconds=settings.fetch_interval_seconds,
    listener=event_listener,
)
telegram_login_service = TelegramLoginService(event_listener, SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap()
    app.state.scheduler = scheduler_service
    app.state.event_listener = event_listener
    app.state.telegram_login = telegram_login_service
    scheduler_service.start()
    if settings.telegram_api_id and settings.telegram_api_hash:
        await event_listener.start(SessionLocal)
    yield
    await event_listener.stop()
    scheduler_service.shutdown()


_is_local = settings.app_env == "local"
app = FastAPI(
    title="Telegram News Bot Admin",
    lifespan=lifespan,
    docs_url="/docs" if _is_local else None,
    redoc_url="/redoc" if _is_local else None,
    openapi_url="/openapi.json" if _is_local else None,
)
# Middleware order: last-added runs first (outermost). Session must run before CSRF.
# So we add CSRF first (inner), then Session (outer).
app.add_middleware(CSRFMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, https_only=not _is_local)
# Снаружи сессии: тогда в строку действия попадает признак входа, и видны даже
# те ответы, которые вернула сама защита от подделки запросов.
app.add_middleware(ActionLogMiddleware)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
# /media is served via an authenticated route in routes.py — not a public StaticFiles mount.
app.include_router(router)


#: Фабрика сессий для записи ошибок в журнал. Своя, а не зависимость запроса:
#: транзакция запроса в момент падения уже может быть испорчена. Тесты её подменяют.
journal_session = SessionLocal

#: Пароль внутри строки подключения: он попадается в текстах ошибок SQLAlchemy.
_SECRET_IN_URL = re.compile(r"://[^:@/\s]+:[^@/\s]+@")


def _without_secrets(text: str) -> str:
    return _SECRET_IN_URL.sub("://<скрыто>@", text)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Любая непойманная ошибка: код оператору, подробности в лог и журнал.

    Раньше здесь не было ничего, и браузер получал от uvicorn строку
    «Internal Server Error» без объяснений, а трейсбек оставался в логе
    контейнера — куда оператор не смотрит и не должен.

    Теперь у каждой ошибки есть короткий код. По нему запись находится и в
    журнале панели, и в логе, так что разговор начинается не с «что-то упало
    где-то», а с конкретного действия.
    """
    code = uuid.uuid4().hex[:6].upper()
    logger.error(
        "Ошибка %s: %s %s от %s — %s",
        code, request.method, request.url.path, who(request), exc, exc_info=True,
    )
    reason = _without_secrets(f"{type(exc).__name__}: {exc}")[:400]
    try:
        with journal_session() as db:
            db.add(ActionLog(
                action="server_error",
                entity_type="Request",
                entity_id=code,
                message=f"{request.method} {request.url.path} → {reason}",
            ))
            db.commit()
    except Exception:
        # Журнал мог стать недоступен вместе с базой — это и могло быть причиной.
        logger.warning("Ошибку %s не удалось записать в журнал", code, exc_info=True)
    return templates.TemplateResponse(request, "error.html", {"code": code}, status_code=500)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Браузер просит /favicon.ico сам, не читая разметку.

    Без этой ручки каждая вкладка оставляла в логе 404, а лог на хостинге
    короткий — в нём и так тесно.
    """
    return FileResponse("app/web/static/favicon.svg", media_type="image/svg+xml")


@app.get("/health", include_in_schema=False)
async def health():
    """Проверка состояния для App Platform: без БД и внешних сервисов."""
    return {"status": "ok"}


@app.get("/health/db", include_in_schema=False)
async def health_db(db: Session = Depends(get_db)):
    """Отдельная проверка базы — специально не внутри /health.

    По /health хостинг решает, жив ли контейнер, и перезапускает его, когда
    ответа нет. Недоступность базы перезапуском не лечится, так что подмешивать
    её туда нельзя: отвалившийся на минуту кластер уронил бы приложение в цикл
    перезапусков. Эта ручка нужна внешнему наблюдению и человеку.

    Текст ошибки наружу не отдаётся: в исключениях SQLAlchemy встречается
    строка подключения вместе с паролем. Подробности уходят в лог.
    """
    started = time.perf_counter()
    try:
        # Сессия создаётся без подключения: соединение открывается этим запросом,
        # поэтому недоступная база видна здесь, а не в обход обработчика.
        db.execute(select(1))
    except Exception:
        logger.warning("Проверка базы не прошла", exc_info=True)
        return JSONResponse(status_code=503, content={"status": "error"})
    return {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 1)}


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path != "/login":
        return RedirectResponse(url="/login", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
