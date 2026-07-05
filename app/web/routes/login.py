from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.web.auth import login_action
from app.web.routes.common import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {
        "csrf_token": request.session.get("csrf_token", ""),
    })


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    return login_action(request, username, password)
