import asyncio
import re
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from app.services.lastfm import lastfm_client
from app.services.listenbrainz import lb_client
from app.services.navidrome import get_collection, get_newest_albums
from app.services.red import get_top_albums as red_top_albums
from app.utils import build_collection_lookup, find_collection_album

router = APIRouter()

_top_cache:  dict = {"data": None, "expires": datetime.min}
_rec_cache:  dict = {"data": None, "expires": datetime.min}
_lb_cache:   dict = {"data": None, "week": None}
_red_cache:  dict = {"data": None, "expires": datetime.min}


def _current_monday() -> str:
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


@router.get("/cache/clear")
async def clear_cache():
    global _top_cache, _rec_cache, _lb_cache, _red_cache
    from datetime import datetime
    _top_cache = {"data": None, "expires": datetime.min}
    _rec_cache = {"data": None, "expires": datetime.min}
    _lb_cache  = {"data": None, "week": None}
    _red_cache = {"data": None, "expires": datetime.min}
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    global _top_cache, _rec_cache, _lb_cache, _red_cache

    now = datetime.now()

    # Top albums — refresh every hour
    if now > _top_cache["expires"]:
        albums = await lastfm_client.get_top_albums(period="7day", limit=12)
        _top_cache = {"data": albums, "expires": now + timedelta(hours=1)}

    # Last.fm album recommendations — refresh daily
    if now > _rec_cache["expires"]:
        recs = await lastfm_client.get_weekly_recommendations(limit=18)
        _rec_cache = {"data": recs, "expires": now + timedelta(hours=2)}


    # RED top albums — refresh every 6 hours
    if now > _red_cache["expires"]:
        red_albums = await red_top_albums(period="week", limit=10)
        _red_cache = {"data": red_albums, "expires": now + timedelta(hours=6)}

    # ListenBrainz weekly playlists — refresh every Monday (or if last fetch was empty)
    week_key = _current_monday()
    if _lb_cache["week"] != week_key or not _lb_cache["data"]:
        playlists = await lb_client.get_weekly_playlists()
        # Enrich each playlist's tracks with covers from Last.fm
        for pl in playlists:
            enriched = await lastfm_client.enrich_tracks_with_covers(pl["tracks"])
            # Deduplicate by album — one card per unique album, skip live/bootleg releases
            _live_re = re.compile(
                r'\blive\b|\bconcert\b|\btour\b|\bbootleg\b|\b\d{4}[-–]\d{2}[-–]\d{2}\b'
                r'|\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
                re.IGNORECASE,
            )
            seen_albums = set()
            albums = []
            for t in enriched:
                album_name = t.get("album", "")
                if not album_name:
                    continue
                if _live_re.search(album_name):
                    continue
                key = f"{t.get('artist','').lower()}|{album_name.lower()}"
                if key in seen_albums:
                    continue
                seen_albums.add(key)
                albums.append({**t, "recommended_track": t.get("title", "")})
            pl["albums"] = albums
        _lb_cache = {"data": playlists, "week": week_key}

    # Recently added + collection — from Navidrome
    collection, recently_added = await asyncio.gather(
        get_collection(),
        get_newest_albums(limit=12),
    )

    collection_lookup = build_collection_lookup(collection)

    def _mark_album(album: dict, *, owned: bool = False) -> dict:
        year = album.get("year") or album.get("release_year") or ""
        match = find_collection_album(
            album.get("artist", ""),
            album.get("album", ""),
            lookup=collection_lookup,
            year=year,
            mb_id=album.get("mb_id", "") or album.get("album_mbid", ""),
            cover_url=album.get("cover_url", ""),
        )
        return {**album, "in_collection": owned or bool(match)}

    def _mark_list(albums: list[dict]) -> list[dict]:
        return [_mark_album(album) for album in albums or []]

    marked_playlists = []
    for playlist in _lb_cache["data"] or []:
        marked_playlists.append({
            **playlist,
            "albums": _mark_list(playlist.get("albums", [])),
        })

    return templates.TemplateResponse("home.html", {
        "request": request,
        "lastfm_albums": _mark_list(_top_cache["data"]),
        "recommendations": _mark_list(_rec_cache["data"]),
        "lb_playlists": marked_playlists,
        "rec_week": week_key,
        "recently_added": [_mark_album(album, owned=True) for album in recently_added],
        "red_top": _mark_list(_red_cache["data"]),
    })
