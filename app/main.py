from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus
from app.services.prompt_settings import ensure_default_prompt_settings
from app.services.scheduler import SchedulerService
from app.web.routes import router

settings = get_settings()
Path("data/media").mkdir(parents=True, exist_ok=True)
Path("data/telegram_session").mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    ensure_default_prompt_settings(db)
    # Reset posts stuck in FAILED due to server crash during publish — mark them back to GENERATED
    # so the operator can retry manually. Only resets if publish_error is None (crash, not API error).
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

scheduler_service = SchedulerService(interval_seconds=settings.fetch_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = scheduler_service
    scheduler_service.start()
    yield
    scheduler_service.shutdown()


app = FastAPI(title="Telegram News Bot Admin", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.mount("/media", StaticFiles(directory="data/media"), name="media")
app.include_router(router)


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path != "/login":
        return RedirectResponse(url="/login", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
