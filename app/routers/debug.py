from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from app.services.navidrome import get_collection

router = APIRouter()


@router.get("/debug/collection", response_class=HTMLResponse)
async def debug_collection(request: Request):
    collection = await get_collection()
    return templates.TemplateResponse("debug_collection.html", {
        "request": request,
        "nav_total": len(collection),
        "collection": sorted(collection, key=lambda a: (a["artist"], a["album"])),
    })
