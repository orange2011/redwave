import asyncio
import urllib.parse
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from app.services.lastfm import lastfm_client, get_artist_image, get_mb_discography
from app.services.navidrome import get_collection
from app.services.album_cache import get_cached_albums_for_artist
from app.utils import build_collection_lookup, find_collection_album, normalize_album_title, normalize_artist

router = APIRouter()


def _release_year(item: dict) -> str:
    release_date = str(item.get("release_date") or "")
    year = str(item.get("year") or "")
    return (release_date[:4] if release_date else year[:4]).strip()


def _release_label(item: dict) -> str:
    raw = str(
        item.get("primary_type")
        or item.get("release_type")
        or item.get("type")
        or "Album"
    ).strip()
    if not raw:
        return "Album"
    lower = raw.lower()
    if lower in {"album", "single", "ep", "compilation", "soundtrack", "live"}:
        return raw[:1].upper() + raw[1:]
    return "Album"


def _release_from_album(item: dict, artist_name: str, source: str) -> dict:
    album = item.get("album") or item.get("name") or item.get("title") or ""
    return {
        "mb_id": item.get("mb_id", ""),
        "album": album,
        "artist": item.get("artist") or artist_name,
        "year": _release_year(item),
        "cover_url": item.get("cover_url", ""),
        "primary_type": _release_label(item),
        "source": source,
    }


def _merge_release(discography: dict[str, list[dict]], label: str, release: dict) -> None:
    key = normalize_album_title(release.get("album", ""))
    if not key:
        return

    for releases in discography.values():
        for existing in releases:
            if normalize_album_title(existing.get("album", "")) != key:
                continue
            for field in ("cover_url", "year", "mb_id"):
                if not existing.get(field) and release.get(field):
                    existing[field] = release[field]
            return

    discography.setdefault(label or "Album", []).append(release)


def merge_artist_discography(
    discography: dict[str, list[dict]],
    artist_name: str,
    collection: list[dict],
    top_albums: list[dict],
    cached_albums: list[dict],
) -> dict[str, list[dict]]:
    """Fill artist pages with every album source Redwave already trusts."""
    merged = {label: [dict(release) for release in releases] for label, releases in discography.items()}
    wanted_artist = normalize_artist(artist_name)

    for album in cached_albums:
        release = _release_from_album(album, artist_name, "cache")
        _merge_release(merged, _release_label(album), release)

    for album in collection:
        if normalize_artist(album.get("artist", "")) != wanted_artist:
            continue
        release = _release_from_album(album, artist_name, "collection")
        _merge_release(merged, _release_label(album), release)

    for album in top_albums:
        release = _release_from_album(album, artist_name, "lastfm")
        _merge_release(merged, _release_label(album), release)

    ordered: dict[str, list[dict]] = {}
    preferred = ["Album", "EP", "Single", "Compilation", "Soundtrack", "Live"]
    for label in preferred + [label for label in merged.keys() if label not in preferred]:
        releases = merged.get(label, [])
        if not releases:
            continue
        ordered[label] = sorted(
            releases,
            key=lambda r: (
                str(r.get("year") or "9999") or "9999",
                normalize_album_title(r.get("album", "")),
            ),
        )
    return ordered


@router.get("/artist/{name}", response_class=HTMLResponse)
async def artist_page(request: Request, name: str):
    artist_name = urllib.parse.unquote_plus(name)

    info, collection = await asyncio.gather(
        lastfm_client.get_artist_info(artist_name),
        get_collection(),
    )

    # Artist image fallback
    if info and not info.get("image"):
        image = await get_artist_image(artist_name)
        if not image:
            image = None
        info["image"] = image

    artist_name = (info or {}).get("name", artist_name)
    mb_id = (info or {}).get("mb_id", "")
    discography, top_albums, cached_albums = await asyncio.gather(
        get_mb_discography(mb_id, artist_name),
        lastfm_client.get_artist_top_albums(artist_name, limit=36),
        get_cached_albums_for_artist(artist_name, limit=100),
    )
    discography = merge_artist_discography(
        discography,
        artist_name,
        collection,
        top_albums,
        cached_albums,
    )

    collection_lookup = build_collection_lookup(collection)

    # Enrich discography entries with collection info
    for label, releases in discography.items():
        for r in releases:
            match = find_collection_album(
                artist_name,
                r.get("album", ""),
                lookup=collection_lookup,
                year=r.get("year", ""),
                mb_id=r.get("mb_id", ""),
                cover_url=r.get("cover_url", ""),
            )
            if match:
                r["in_collection"] = True
                r["nav_cover"] = match.get("cover_url", "")
            else:
                r["in_collection"] = False
                r["nav_cover"] = ""

    q = urllib.parse.quote(artist_name)
    external_links = [
        {"name": "Last.fm",      "url": (info or {}).get("url") or f"https://www.last.fm/music/{q}",                                           "color": "#D51007"},
        {"name": "MusicBrainz",  "url": f"https://musicbrainz.org/artist/{mb_id}" if mb_id else f"https://musicbrainz.org/search?query={q}&type=artist", "color": "#BA478F"},
        {"name": "ListenBrainz", "url": f"https://listenbrainz.org/artist/{mb_id}" if mb_id else f"https://listenbrainz.org/search/?search_term={q}",     "color": "#353070"},
        {"name": "Spotify",      "url": f"https://open.spotify.com/search/{q}",                                                                "color": "#1DB954"},
        {"name": "Qobuz",        "url": f"https://www.qobuz.com/us-en/search/artists/{q}",                                                     "color": "#0070E0"},
        {"name": "Discogs",      "url": f"https://www.discogs.com/search/?q={q}&type=artist",                                                   "color": "#F5A623"},
        {"name": "YouTube",      "url": f"https://www.youtube.com/results?search_query={q}",                                                    "color": "#FF0000"},
    ]

    return templates.TemplateResponse("artist.html", {
        "request": request,
        "artist": artist_name,
        "info": info or {},
        "discography": discography,
        "external_links": external_links,
    })
