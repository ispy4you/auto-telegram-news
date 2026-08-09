from fastapi import APIRouter

from app.web.routes import dashboard, generated, login, logs, media, posts, projects, source_routes, settings, sources, stats, targets, telegram_login

router = APIRouter()

for module in (login, media, dashboard, sources, targets, source_routes, posts, generated, settings, projects, stats, logs, telegram_login):
    router.include_router(module.router)
