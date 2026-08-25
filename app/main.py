import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select, text, update

from app.config import get_settings
from app.database import SessionLocal
from app.models import GeneratedPost, GeneratedPostStatus, Project, RawPost, RawPostStatus
from app.migrations import run_migrations
from app.services import telegram_session_store
from app.services.prompt_settings import ensure_default_prompt_settings
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
Path("data/media").mkdir(parents=True, exist_ok=True)
run_migrations()

# ── Убедиться что дефолтный проект существует (ORM, без диалект-специфичного SQL)
with SessionLocal() as db:
    if not db.get(Project, 1):
        db.add(Project(
            id=1, name="Default", slug="default", enabled=True,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        db.commit()
    db.execute(text("UPDATE source_channels SET project_id = 1 WHERE project_id IS NULL"))
    db.execute(text("UPDATE target_channels SET project_id = 1 WHERE project_id IS NULL"))
    db.commit()

with SessionLocal() as db:
    ensure_default_prompt_settings(db)
    db.execute(
        update(RawPost)
        .where(RawPost.status == RawPostStatus.FAILED.value)
        .values(status=RawPostStatus.GENERATED.value)
    )
    db.execute(
        update(GeneratedPost)
        .where(GeneratedPost.status == GeneratedPostStatus.FAILED.value, GeneratedPost.publish_error.is_(None))
        .values(status=GeneratedPostStatus.DRAFT.value)
    )
    db.commit()

# Установки, логинившиеся до переезда сессии в БД, переносим автоматически.
telegram_session_store.migrate_legacy_file(settings.telegram_session_path)

event_listener = TelegramEventListenerService()
scheduler_service = SchedulerService(
    interval_seconds=settings.fetch_interval_seconds,
    listener=event_listener,
)
telegram_login_service = TelegramLoginService(event_listener, SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
