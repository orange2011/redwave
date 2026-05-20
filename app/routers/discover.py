import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.services.discovery import (
    GENRE_PRESETS,
    artist_gap_targets,
    coerce_genre,
    collection_keys,
    genre_slug,
    mark_collection,
    missing_albums_for_artist,
)
from app.services.lastfm import lastfm_client
from app.services.navidrome import get_collection, refresh_collection
from app.templates_config import templates

router = APIRouter(prefix="/discover")

_genre_cache: dict[str, dict] = {}
_gap_cache: dict[tuple, dict] = {}
_GENRE_TTL = timedelta(hours=6)
_GAP_TTL = timedelta(hours=4)


def _collection_signature(collection: list[dict]) -> str:
    latest = max((album.get("added_at") or 0 for album in collection), default=0)
    return f"{len(collection)}:{latest:.0f}"


def _clear_discover_caches() -> None:
    _genre_cache.clear()
    _gap_cache.clear()


async def _genre_charts(genre: str) -> tuple[list[dict], list[dict]]:
    now = datetime.now()
    cached = _genre_cache.get(genre)
    if cached and now < cached["expires"]:
        return cached["albums"], cached["tracks"]

    albums, tracks = await asyncio.gather(
        lastfm_client.get_tag_top_albums(genre, limit=30),
        lastfm_client.get_tag_top_tracks(genre, limit=12),
    )
    _genre_cache[genre] = {
        "albums": albums,
        "tracks": tracks,
        "expires": now + _GENRE_TTL,
    }
    return albums, tracks


async def _library_gap_groups(
    collection: list[dict],
    artist_filter: str,
    max_artists: int,
    albums_per_artist: int,
) -> list[dict]:
    signature = _collection_signature(collection)
    cache_key = (artist_filter.strip().lower(), max_artists, albums_per_artist, signature)
    now = datetime.now()
    cached = _gap_cache.get(cache_key)
    if cached and now < cached["expires"]:
        return cached["groups"]

    owned_keys = collection_keys(collection)
    targets = artist_gap_targets(
        collection,
        artist_filter=artist_filter,
        max_artists=max_artists * 2 if not artist_filter else max_artists,
    )
    album_lists = await asyncio.gather(*[
        lastfm_client.get_artist_top_albums(target["artist"], limit=albums_per_artist + 8)
        for target in targets
    ])

    groups = []
    for target, albums in zip(targets, album_lists):
        missing = missing_albums_for_artist(
            target["artist"],
            albums,
            owned_keys,
            limit=albums_per_artist,
        )
        if not missing:
            continue
        groups.append({
            **target,
            "missing": missing,
        })
        if len(groups) >= max_artists:
            break

    _gap_cache[cache_key] = {"groups": groups, "expires": now + _GAP_TTL}
    return groups


@router.get("/", response_class=HTMLResponse)
async def discover_index():
    return RedirectResponse("/discover/genres")


@router.get("/genres", response_class=HTMLResponse)
async def discover_genres(
    request: Request,
    genre: str = Query(default="metal"),
):
    active_genre = coerce_genre(genre)
    collection, charts = await asyncio.gather(
        get_collection(),
        _genre_charts(active_genre),
    )
    owned_keys = collection_keys(collection)
    albums, tracks = charts

    return templates.TemplateResponse("discover.html", {
        "request": request,
        "mode": "genres",
        "genres": GENRE_PRESETS,
        "genre_slug": genre_slug,
        "active_genre": active_genre,
        "genre_albums": mark_collection(albums, owned_keys),
        "genre_tracks": tracks,
        "gap_groups": [],
        "gap_query": "",
        "gap_total_artists": 0,
        "gap_total_albums": 0,
        "collection_signature": _collection_signature(collection),
    })


@router.get("/library", response_class=HTMLResponse)
async def discover_library(
    request: Request,
    artist: str = Query(default=""),
):
    collection = await get_collection()
    gap_groups = await _library_gap_groups(
        collection,
        artist_filter=artist,
        max_artists=12,
        albums_per_artist=6,
    )

    return templates.TemplateResponse("discover.html", {
        "request": request,
        "mode": "library",
        "genres": GENRE_PRESETS,
        "genre_slug": genre_slug,
        "active_genre": "",
        "genre_albums": [],
        "genre_tracks": [],
        "gap_groups": gap_groups,
        "gap_query": artist,
        "gap_total_artists": len({(album.get("artist") or "").strip().lower() for album in collection if album.get("artist")}),
        "gap_total_albums": len(collection),
        "collection_signature": _collection_signature(collection),
    })


@router.post("/api/refresh")
async def refresh_discover():
    collection = await refresh_collection()
    _clear_discover_caches()
    return JSONResponse({
        "ok": True,
        "signature": _collection_signature(collection),
        "albums": len(collection),
        "artists": len({(album.get("artist") or "").strip().lower() for album in collection if album.get("artist")}),
    })


@router.get("/api/state")
async def discover_state():
    collection = await refresh_collection()
    return JSONResponse({
        "signature": _collection_signature(collection),
        "albums": len(collection),
        "artists": len({(album.get("artist") or "").strip().lower() for album in collection if album.get("artist")}),
    })
