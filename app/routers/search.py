import asyncio
import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from app.services.lastfm import lastfm_client
from app.services.url_import import resolve_url, is_url
from app.services.navidrome import get_collection

router = APIRouter()


def _search_collection(q: str, collection: list[dict]) -> list[dict]:
    ql = q.lower()
    matches = []
    for a in collection:
        if ql in a["artist"].lower() or ql in a["album"].lower():
            matches.append({
                "artist": a["artist"],
                "album": a["album"],
                "mb_id": a.get("mb_id", ""),
                "cover_url": a.get("cover_url", ""),
                "in_collection": True,
            })
    return matches[:8]


async def _search_musicbrainz(q: str, limit: int = 10) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://musicbrainz.org/ws/2/release-group",
                params={"query": q, "fmt": "json", "limit": limit},
                headers={"User-Agent": "Redwave/1.0 (redwave@localhost)"},
            )
            rgs = r.json().get("release-groups", [])
            results = []
            for rg in rgs:
                credits = rg.get("artist-credit") or []
                artist = credits[0].get("artist", {}).get("name", "") if credits else ""
                album = rg.get("title", "")
                mb_id = rg.get("id", "")
                if artist and album:
                    results.append({
                        "artist": artist,
                        "album": album,
                        "mb_id": mb_id,
                        "cover_url": None,
                        "source": "mb",
                    })
            return results
    except Exception:
        return []


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(default="")):
    results = []
    artists = []
    track_results = []
    collection_hits = []

    if q and len(q) >= 2:
        if is_url(q):
            info = await resolve_url(q)
            if info and info.get("artist") and info.get("album"):
                lfm = await lastfm_client.search_albums(
                    f"{info['artist']} {info['album']}", limit=5
                )
                artist_l = info["artist"].lower()
                album_l = info["album"].lower()
                matched = next(
                    (r for r in lfm if r["artist"].lower() == artist_l and r["album"].lower() == album_l),
                    lfm[0] if lfm else None,
                )
                if matched:
                    results = [{
                        **matched,
                        "cover_url": info["cover_url"] or matched.get("cover_url"),
                        "deezer_id": info.get("deezer_id", ""),
                    }]
                else:
                    results = [{
                        "mb_id": info.get("mb_id", ""),
                        "artist": info["artist"],
                        "album": info["album"],
                        "cover_url": info.get("cover_url"),
                        "deezer_id": info.get("deezer_id", ""),
                    }]
        else:
            # Collection hits + Last.fm albums + artists + tracks in parallel
            collection, (lfm_results, artists, track_results) = await asyncio.gather(
                get_collection(),
                asyncio.gather(
                    lastfm_client.search_albums(q, limit=20),
                    lastfm_client.search_artists(q, limit=5),
                    lastfm_client.search_tracks(q, limit=10),
                ),
            )
            collection_hits = _search_collection(q, collection)

            # Deduplicate Last.fm results against collection hits
            col_keys = {f"{h['artist'].lower()}|{h['album'].lower()}" for h in collection_hits}
            results = [
                r for r in lfm_results
                if f"{r['artist'].lower()}|{r['album'].lower()}" not in col_keys
            ]

            # If Last.fm came up short, pad with MusicBrainz
            if len(results) < 5:
                mb_results = await _search_musicbrainz(q, limit=10)
                mb_keys = col_keys | {f"{r['artist'].lower()}|{r['album'].lower()}" for r in results}
                for r in mb_results:
                    key = f"{r['artist'].lower()}|{r['album'].lower()}"
                    if key not in mb_keys:
                        results.append(r)
                        mb_keys.add(key)

    ctx = {
        "request": request,
        "results": results,
        "artists": artists,
        "track_results": track_results,
        "collection_hits": collection_hits,
        "query": q,
        "url_not_supported": is_url(q) and not results,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/search_results.html", ctx)
    return templates.TemplateResponse("search.html", ctx)
