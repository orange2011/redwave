from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templates_config import templates
from app.config import settings

router = APIRouter()

PUBLIC_PATHS = {"/login", "/static"}


def is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated") is True


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == settings.app_username and password == settings.app_password:
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid username or password",
    })


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
