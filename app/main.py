from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.database import init_db
from app.models import cache as _cache_model  # noqa: F401 — registers AlbumCache table
from app.routers import home, search, album, artist, collection, debug, auth
from app.routers.api import torrents, youtube, navidrome as navidrome_api
from app.routers import settings_page
from app.tasks.status_poller import poll_active_downloads


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_active_downloads, "interval", seconds=30)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Redwave", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path in ("/login", "/logout"):
        return await call_next(request)
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(search.router)
app.include_router(album.router)
app.include_router(artist.router)
app.include_router(collection.router)
app.include_router(torrents.router)
app.include_router(youtube.router)
app.include_router(navidrome_api.router)
app.include_router(settings_page.router)
app.include_router(debug.router)
