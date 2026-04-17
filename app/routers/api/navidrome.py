from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse, Response
from app.services.navidrome import trigger_scan, get_cover_bytes, refresh_collection

router = APIRouter(prefix="/api")


async def _scan():
    ok = await trigger_scan()
    if ok:
        # Bust the collection cache so new albums appear immediately
        await refresh_collection()
        return JSONResponse({"ok": True, "msg": "Navidrome scan started"})
    return JSONResponse({"ok": False, "msg": "Scan failed or Navidrome not configured"}, status_code=500)


@router.get("/navidrome/scan")
async def navidrome_scan_get():
    return await _scan()


@router.post("/navidrome/scan")
async def navidrome_scan_post(request: Request):
    return await _scan()


@router.get("/navidrome/cover/{cover_art_id}")
async def navidrome_cover(cover_art_id: str):
    result = await get_cover_bytes(cover_art_id)
    if result:
        data, mime = result
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "public, max-age=604800"})
    return Response(status_code=404)
