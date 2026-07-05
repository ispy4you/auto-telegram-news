import urllib.parse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, SourceChannel, TargetChannel
from app.web.auth import require_auth
from app.web.routes.common import tpl

router = APIRouter()


@router.get("/projects")
def projects_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), ok: str | None = None, error: str | None = None):
    items = db.scalars(select(Project).order_by(Project.name)).all()
    return tpl(request, "projects.html", db, {"items": items, "ok": ok, "error": error})


@router.post("/projects")
def create_project(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    slug = name.lower().replace(" ", "-").replace("_", "-")[:64]
    existing = db.scalars(select(Project).where(Project.slug == slug)).first()
    if existing:
        slug = f"{slug}-{db.scalar(select(func.count()).select_from(Project)) or 0}"
    project = Project(name=name, slug=slug, enabled=True)
    db.add(project)
    db.commit()
    db.refresh(project)
    request.session["current_project_id"] = project.id
    return RedirectResponse(url="/projects", status_code=302)


@router.post("/projects/{project_id}/delete")
def delete_project(project_id: int, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    project = db.get(Project, project_id)
    if not project:
        return RedirectResponse(url="/projects", status_code=302)
    has_sources = db.scalar(select(func.count()).select_from(SourceChannel).where(SourceChannel.project_id == project_id)) or 0
    has_targets = db.scalar(select(func.count()).select_from(TargetChannel).where(TargetChannel.project_id == project_id)) or 0
    if has_sources or has_targets:
        return RedirectResponse(url=f"/projects?error={urllib.parse.quote('Нельзя удалить: в проекте есть источники или каналы')}", status_code=302)
    db.delete(project)
    db.commit()
    if request.session.get("current_project_id") == project_id:
        request.session.pop("current_project_id", None)
    return RedirectResponse(url="/projects", status_code=302)


@router.post("/projects/{project_id}/rename")
def rename_project(
    project_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    project = db.get(Project, project_id)
    if project and name.strip():
        project.name = name.strip()
        db.commit()
    return RedirectResponse(url="/projects", status_code=302)


@router.post("/switch-project")
def switch_project(request: Request, project_id: int = Form(...), db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    if db.get(Project, project_id):
        request.session["current_project_id"] = project_id
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=302)
