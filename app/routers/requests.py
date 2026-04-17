from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.models.request import AlbumRequest
from app.database import get_db

router = APIRouter()


@router.get("/requests", response_class=HTMLResponse)
async def requests_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlbumRequest).order_by(desc(AlbumRequest.created_at))
    )
    all_requests = result.scalars().all()

    return templates.TemplateResponse("requests_queue.html", {
        "request": request,
        "requests": all_requests,
    })
