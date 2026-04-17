import io
import urllib.parse
from pathlib import Path
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from app.templates_config import templates
from app.services.navidrome import get_collection, refresh_collection
from app.services.scanner import MUSIC_DIR, COVER_NAMES, AUDIO_EXTS

router = APIRouter()


@router.get("/collection", response_class=HTMLResponse)
async def collection_page(request: Request, q: str = Query(default="")):
    albums = await get_collection()
    if q:
        ql = q.lower()
        albums = [a for a in albums if ql in a["artist"].lower() or ql in a["album"].lower()]

    return templates.TemplateResponse("collection.html", {
        "request": request,
        "albums": albums,
        "query": q,
        "total": len(albums),
    })


@router.get("/api/collection/cover")
async def serve_cover(folder: str = Query(...), embedded: int = Query(default=0)):
    """Serve cover art: cover file → embedded FLAC tag → Last.fm redirect."""
    target = (MUSIC_DIR / folder).resolve()
    if not str(target).startswith(str(MUSIC_DIR.resolve())):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # 1. Cover file on disk
    if not embedded:
        for name in COVER_NAMES:
            cover = target / name
            if cover.exists():
                return FileResponse(str(cover), media_type="image/jpeg")

    # 2. Embedded art in first audio file
    try:
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3
        audio_file = next(
            (f for f in sorted(target.iterdir()) if f.suffix.lower() in AUDIO_EXTS),
            None
        )
        if audio_file:
            if audio_file.suffix.lower() == ".flac":
                tags = FLAC(str(audio_file))
                if tags.pictures:
                    pic = tags.pictures[0]
                    return StreamingResponse(io.BytesIO(pic.data), media_type=pic.mime or "image/jpeg")
            else:
                tags = ID3(str(audio_file))
                apic = tags.getall("APIC")
                if apic:
                    return StreamingResponse(io.BytesIO(apic[0].data), media_type=apic[0].mime or "image/jpeg")
    except Exception:
        pass

    # 3. Last.fm fallback — derive artist/album from folder path
    from app.services.scanner import _parse_folder
    import os
    parts = folder.replace("\\", "/").split("/")
    artist_str, album_str = "", ""
    if len(parts) >= 2:
        # Artist/Album subfolder structure
        artist_str = parts[-2]
        album_str = parts[-1]
    else:
        parsed = _parse_folder(parts[-1])
        if parsed:
            artist_str = parsed["artist"]
            album_str = parsed["album"]

    if artist_str and album_str:
        artist_enc = urllib.parse.quote(artist_str)
        album_enc = urllib.parse.quote(album_str)
        return RedirectResponse(f"/api/collection/cover_lfm?artist={artist_enc}&album={album_enc}")

    return JSONResponse({"error": "no cover found"}, status_code=404)


@router.get("/api/collection/cover_lfm")
async def cover_lastfm(artist: str = Query(...), album: str = Query(...)):
    """Fetch cover URL from Last.fm → Deezer → Discogs and redirect to it."""
    from app.services.lastfm import lastfm_client, _cover_from_discogs

    # 1. Last.fm
    try:
        info = await lastfm_client.get_album_info(artist, album)
        cover = (info or {}).get("cover_url")
        if cover:
            return RedirectResponse(cover)
    except Exception:
        pass

    # 2. Deezer search
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get("https://api.deezer.com/search/album", params={"q": f"{artist} {album}", "limit": 5})
            items = r.json().get("data", [])
            for item in items:
                t = (item.get("title") or "").lower()
                if album.lower()[:10] in t:
                    cover = item.get("cover_xl") or item.get("cover_big") or item.get("cover_medium")
                    if cover:
                        return RedirectResponse(cover)
    except Exception:
        pass

    # 3. Discogs
    try:
        cover = await _cover_from_discogs(artist, album)
        if cover:
            return RedirectResponse(cover)
    except Exception:
        pass

    return JSONResponse({"error": "not found"}, status_code=404)


@router.post("/api/collection/refresh", response_class=HTMLResponse)
async def do_refresh(request: Request):
    albums = await refresh_collection()
    return templates.TemplateResponse("partials/collection_stats.html", {
        "request": request,
        "total": len(albums),
    })
