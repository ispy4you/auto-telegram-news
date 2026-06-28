from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import text

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus
from app.services.prompt_settings import ensure_default_prompt_settings
from app.services.scheduler import SchedulerService
from app.services.telegram_event_listener import TelegramEventListenerService
from app.web.csrf import CSRFMiddleware
from app.web.routes import router

settings = get_settings()
Path("data/media").mkdir(parents=True, exist_ok=True)
Path("data/telegram_session").mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

with engine.connect() as _conn:
    for _tbl, _col, _typ in [
        ("target_channels", "publish_from", "TEXT"),
        ("target_channels", "publish_to", "TEXT"),
        ("generated_posts", "telegram_message_id", "INTEGER"),
        ("source_channels", "project_id", "INTEGER"),
        ("target_channels", "project_id", "INTEGER"),
        ("raw_posts", "embedding", "TEXT"),
    ]:
        try:
            _conn.execute(text(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_typ}"))
            _conn.commit()
        except Exception:
            pass
    # Ensure default project exists and all legacy rows are assigned to it
    _conn.execute(text(
        "INSERT OR IGNORE INTO projects (id, name, slug, enabled, created_at) "
        "VALUES (1, 'Default', 'default', 1, datetime('now'))"
    ))
    _conn.execute(text("UPDATE source_channels SET project_id = 1 WHERE project_id IS NULL"))
    _conn.execute(text("UPDATE target_channels SET project_id = 1 WHERE project_id IS NULL"))
    _conn.commit()

with SessionLocal() as db:
    ensure_default_prompt_settings(db)
    from sqlalchemy import select, update
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

event_listener = TelegramEventListenerService()
scheduler_service = SchedulerService(
    interval_seconds=settings.fetch_interval_seconds,
    listener=event_listener,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = scheduler_service
    app.state.event_listener = event_listener
    scheduler_service.start()
    if settings.telegram_api_id and settings.telegram_api_hash and settings.telegram_session_path:
        await event_listener.start(SessionLocal)
    yield
    await event_listener.stop()
    scheduler_service.shutdown()


app = FastAPI(title="Telegram News Bot Admin", lifespan=lifespan)
# Middleware order: last-added runs first (outermost). Session must run before CSRF.
# So we add CSRF first (inner), then Session (outer).
app.add_middleware(CSRFMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
# /media is served via an authenticated route in routes.py — not a public StaticFiles mount.
app.include_router(router)


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path != "/login":
        return RedirectResponse(url="/login", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
