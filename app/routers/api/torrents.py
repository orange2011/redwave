import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.services.redacted import red_client
from app.models.request import AlbumRequest, TorrentOption
from app.database import get_db
from app.utils import normalize_album

router = APIRouter(prefix="/api")


def _age_days(time_str: str) -> int:
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0


def _fmt_size(b: int) -> str:
    if b >= 1_073_741_824:
        return f"{b/1_073_741_824:.1f} GB"
    return f"{b/1_048_576:.0f} MB"


@router.get("/torrents/search", response_class=HTMLResponse)
async def search_torrents(
    request: Request,
    mb_id: str = Query(...),
    artist: str = Query(...),
    album: str = Query(...),
    year: str = Query(default=""),
    cover_url: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    search_album = normalize_album(album)
    try:
        results = await red_client.search_torrents(artist, search_album)
    except ValueError as e:
        return templates.TemplateResponse("partials/torrent_picker.html", {
            "request": request, "torrents": [], "error": str(e),
            "mb_id": mb_id, "artist": artist, "album": album,
            "year": year, "cover_url": cover_url,
        })
    except Exception:
        results = []

    torrent_list = []
    for group in results:
        group_id  = group.get("groupId")
        g_artist  = group.get("artist", artist)
        g_album   = group.get("groupName", album)
        g_year    = group.get("groupYear", year)
        for t in group.get("torrents", []):
            fmt      = t.get("format", "")
            encoding = t.get("encoding", "")
            media    = t.get("media", "")
            remaster = t.get("remasterTitle", "")
            has_log  = t.get("hasLog", False)
            log_score = t.get("logScore", 0)
            has_cue  = t.get("hasCue", False)
            free     = t.get("freeTorrent", False)
            seeders  = t.get("seeders", 0)
            leechers = t.get("leechers", 0)
            size     = t.get("size", 0)
            age      = _age_days(t.get("time", ""))

            # Build title similar to Lidarr
            parts = [f"{g_artist} - {g_album}"]
            if g_year:
                parts.append(f"[{g_year}]")
            if remaster:
                parts.append(f"[{remaster}]")
            label = f"{fmt} {encoding}".strip()
            if media:
                label += f" / {media}"
            if has_log:
                label += f" / Log ({log_score}%)"
            if has_cue:
                label += " / Cue"
            parts.append(f"[{label}]")
            title = " ".join(parts)

            torrent_list.append({
                "red_torrent_id": t.get("torrentId"),
                "red_group_id":   group_id,
                "title":          title,
                "format":         fmt,
                "encoding":       encoding,
                "media":          media,
                "size_bytes":     size,
                "size_human":     _fmt_size(size),
                "seeders":        seeders,
                "leechers":       leechers,
                "age_days":       age,
                "freeleech":      free,
                "has_log":        has_log,
                "log_score":      log_score,
                "has_cue":        has_cue,
                "uploader":       t.get("username", ""),
            })

    torrent_list.sort(key=lambda x: (
        0 if x["format"] == "FLAC" else 1,
        0 if x["encoding"] in ("Lossless", "24bit Lossless") else 1,
        -x["seeders"],
    ))

    return templates.TemplateResponse("partials/torrent_picker.html", {
        "request": request,
        "torrents": torrent_list,
        "mb_id": mb_id,
        "artist": artist,
        "album": album,
        "year": year,
        "cover_url": cover_url,
    })


@router.post("/torrents/grab", response_class=HTMLResponse)
async def grab_torrent(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.services.qbittorrent import qbt_client

    form = await request.form()
    mb_id = form.get("mb_id", "")
    artist = form.get("artist", "")
    album = form.get("album", "")
    year = form.get("year", "")
    cover_url = form.get("cover_url", "")
    red_torrent_id = int(form.get("red_torrent_id", 0))
    red_group_id = int(form.get("red_group_id", 0))
    fmt = form.get("format", "")
    encoding = form.get("encoding", "")
    size_bytes = int(form.get("size_bytes", 0))
    seeders = int(form.get("seeders", 0))

    # Create or get existing request
    result = await db.execute(
        select(AlbumRequest).where(AlbumRequest.musicbrainz_id == mb_id)
    )
    album_request = result.scalar_one_or_none()
    if not album_request:
        album_request = AlbumRequest(
            musicbrainz_id=mb_id,
            artist=artist,
            album=album,
            year=year,
            cover_url=cover_url,
            status="downloading",
        )
        db.add(album_request)
        await db.flush()
    else:
        album_request.status = "downloading"

    torrent_option = TorrentOption(
        request_id=album_request.id,
        red_torrent_id=red_torrent_id,
        red_group_id=red_group_id,
        format=fmt,
        encoding=encoding,
        size_bytes=size_bytes,
        seeders=seeders,
    )
    db.add(torrent_option)
    await db.flush()

    # Download torrent file and send to qBittorrent
    try:
        torrent_bytes = await red_client.get_torrent_file(red_torrent_id)
        success = await qbt_client.add_torrent(torrent_bytes)
        if success:
            album_request.status = "downloading"
        else:
            album_request.status = "failed"
    except Exception as e:
        album_request.status = "failed"

    # Add artist to Lidarr as unmonitored so it processes the download
    if album_request.status == "downloading":
        try:
            from app.services.lidarr import add_artist_unmonitored
            await add_artist_unmonitored(artist)
        except Exception:
            pass  # Lidarr is optional — don't fail the grab

    await db.commit()

    return templates.TemplateResponse("partials/grab_response.html", {
        "request": request,
        "album_request": album_request,
        "artist": artist,
        "album": album,
    })
