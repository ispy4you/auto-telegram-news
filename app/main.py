import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus
from app.migrations import run_migrations
from app.services import telegram_session_store
from app.services.scheduler import SchedulerService
from app.services.telegram_event_listener import TelegramEventListenerService
from app.services.telegram_login import TelegramLoginService
from app.web.csrf import CSRFMiddleware
from app.web.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Библиотеки логируют довольно шумно на INFO — приглушаем, оставляя WARNING+.
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

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
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
# /media is served via an authenticated route in routes.py — not a public StaticFiles mount.
app.include_router(router)


@app.get("/health", include_in_schema=False)
async def health():
    """Проверка состояния для App Platform: без БД и внешних сервисов."""
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path != "/login":
        return RedirectResponse(url="/login", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
