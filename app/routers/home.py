from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from app.services.home_cache import (
    clear_home_cache,
    get_home_cache_snapshot,
    schedule_home_cache_refresh_if_stale,
)
from app.utils import build_collection_lookup, find_collection_album

router = APIRouter()


@router.get("/cache/clear")
async def clear_cache():
    clear_home_cache()
    schedule_home_cache_refresh_if_stale(force=True)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    schedule_home_cache_refresh_if_stale()
    snapshot = get_home_cache_snapshot()
    collection = snapshot["collection"]
    recently_added = snapshot["recently_added"]

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
    for playlist in snapshot["lb_playlists"]:
        marked_playlists.append({
            **playlist,
            "albums": _mark_list(playlist.get("albums", [])),
        })

    return templates.TemplateResponse("home.html", {
        "request": request,
        "lastfm_albums": _mark_list(snapshot["lastfm_albums"]),
        "recommendations": _mark_list(snapshot["recommendations"]),
        "lb_playlists": marked_playlists,
        "rec_week": snapshot["rec_week"],
        "recently_added": [_mark_album(album, owned=True) for album in recently_added],
        "red_top": _mark_list(snapshot["red_top"]),
        "tracker_top_label": snapshot["tracker_top_label"],
        "tracker_top_site": snapshot["tracker_top_site"],
    })
