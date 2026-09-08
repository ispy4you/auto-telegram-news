from fastapi import APIRouter

from app.web.routes import compose, dashboard, generated, login, logs, media, posts, projects, prompts, source_routes, settings, sources, stats, targets, telegram_login

router = APIRouter()

for module in (login, media, dashboard, sources, targets, source_routes, posts, compose, generated, prompts, settings, projects, stats, logs, telegram_login):
    router.include_router(module.router)
