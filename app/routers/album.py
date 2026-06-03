import asyncio
import re
import urllib.parse
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.services.lastfm import lastfm_client, get_mb_release_date, get_deezer_album_info, get_tracklist_with_fallback, _cover_from_discogs
from app.services.platforms import get_platform_links
from app.services.album_cache import get_cached_album, save_album_cache
from app.models.request import AlbumRequest
from app.database import get_db
from app.utils import find_collection_album, normalize_artist
from app.services.navidrome import get_album_tracks, get_collection
from app.services.redacted import red_client

router = APIRouter()
_DASH_SPLIT_RE = re.compile(r"\s+-\s+")
_WRAPPING_QUOTES = " \t\r\n\"'`“”„‟«»"


def _strip_wrapping_quotes(value: str) -> str:
    return (value or "").strip(_WRAPPING_QUOTES)


def _collection_identity_candidates(artist: str, album: str) -> list[tuple[str, str]]:
    """Return conservative artist/album variants for messy imported metadata."""
    artist = (artist or "").strip()
    album = (album or "").strip()
    artists = [artist]
    artist_parts = [part.strip() for part in _DASH_SPLIT_RE.split(artist) if part.strip()]
    if len(artist_parts) > 1:
        artists.append(artist_parts[-1])

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate_artist in artists:
        album_variants = [album, _strip_wrapping_quotes(album)]
        album_parts = _DASH_SPLIT_RE.split(album, maxsplit=1)
        if (
            len(album_parts) == 2
            and normalize_artist(album_parts[0]) == normalize_artist(candidate_artist)
        ):
            album_variants.append(_strip_wrapping_quotes(album_parts[1]))

        for candidate_album in album_variants:
            key = (candidate_artist, candidate_album)
            if candidate_artist and candidate_album and key not in seen:
                pairs.append(key)
                seen.add(key)
    return pairs


def _find_collection_album_with_cleanup(
    artist: str,
    album: str,
    collection: list[dict],
    *,
    year: str = "",
    mb_id: str = "",
    cover_url: str = "",
) -> dict | None:
    for candidate_artist, candidate_album in _collection_identity_candidates(artist, album):
        match = find_collection_album(
            candidate_artist,
            candidate_album,
            collection,
            year=year,
            mb_id=mb_id,
            cover_url=cover_url,
        )
        if match:
            return match
    return None


async def _tracker_album_title_hint(artist: str, album: str, year: str) -> str:
    if not artist or not album or not year:
        return album
    try:
        groups = await red_client.search_torrents(artist, album)
    except Exception:
        return album

    for group in groups:
        if str(group.get("groupYear") or "")[:4] != str(year)[:4]:
            continue
        group_name = (group.get("groupName") or "").strip()
        if group_name:
            return group_name
    return album


def _compact_count(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B".replace(".0B", "B")
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 1_000:
        return f"{value / 1_000:.1f}K".replace(".0K", "K")
    return str(value)


def _attach_global_track_popularity(tracks: list[dict], stats: dict[str, dict[str, int]]) -> tuple[list[dict], int]:
    if not tracks:
        return tracks, 0

    enriched = []
    max_count = 0
    for track in tracks:
        name = (track.get("name") or track.get("track") or "").strip()
        track_stats = stats.get(name.lower(), {}) if name else {}
        playcount = int(track_stats.get("playcount") or 0)
        listeners = int(track_stats.get("listeners") or 0)
        max_count = max(max_count, playcount)
        enriched.append({
            **track,
            "global_playcount": playcount,
            "global_listeners": listeners,
            "global_playcount_display": _compact_count(playcount),
        })
    return enriched, max_count


@router.get("/album/{mb_id}", response_class=HTMLResponse)
async def album_detail(
    request: Request,
    mb_id: str,
    artist: str = Query(default=""),
    album: str = Query(default=""),
    year: str = Query(default=""),
    cover: str = Query(default=""),
    deezer_id: str = Query(default=""),
    highlight: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    real_mb_id = mb_id if mb_id != "_" else ""
    requested_year = year.strip()[:4]
    collection = await get_collection()
    initial_collection_match = _find_collection_album_with_cleanup(
        artist,
        album,
        collection,
        year=requested_year,
        mb_id=real_mb_id,
        cover_url=cover,
    )
    if initial_collection_match:
        artist = initial_collection_match.get("artist") or artist
        album = initial_collection_match.get("album") or album
        if not requested_year:
            requested_year = str(initial_collection_match.get("year") or "").strip()[:4]
            year = requested_year
        cover = cover or initial_collection_match.get("cover_url", "")

    if (
        artist
        and album
        and requested_year
        and not initial_collection_match
    ):
        album = await _tracker_album_title_hint(artist, album, requested_year)

    # Check SQLite cache (skip if deezer_id since that's a direct import with fresh data)
    cached = None
    if artist and album and not deezer_id:
        cached = await get_cached_album(artist, album, year=year, mb_id=real_mb_id)

    if cached:
        artist_name  = cached["artist"]
        album_title  = cached["album"]
        year         = cached.get("year") or requested_year
        release_date = cached["release_date"]
        cover_url    = cached.get("cover_url") or cover or None
        tracks       = cached["tracks"]
        tags         = cached["tags"]
        label        = cached["label"]
        genre        = cached["genre"]
        listeners    = cached["listeners"]
        playcount    = cached["playcount"]
        release_type = cached["release_type"]
        platform_links = cached["platform_links"]
        yt_url       = cached["yt_url"]
        most_played_track = cached.get("most_played_track", "")
    else:
        info, itunes, mb_date, deezer = await asyncio.gather(
            lastfm_client.get_album_info(artist, album) if artist and album else asyncio.sleep(0),
            lastfm_client.get_itunes_info(artist, album) if artist and album and not deezer_id else asyncio.sleep(0),
            get_mb_release_date(real_mb_id),
            get_deezer_album_info(deezer_id),
        )

        raw_artist = (deezer or {}).get("artist") or (info or {}).get("artist") or artist
        artist_name = re.split(r'\s*(?:&|,|x|featuring|feat\.?|ft\.?|with)\s+', raw_artist, flags=re.IGNORECASE)[0].strip()
        album_title = (deezer or {}).get("album") or (info or {}).get("album") or album
        cover_url = (deezer or {}).get("cover_url") or (info or {}).get("cover_url") or cover or None
        if not cover_url and artist_name and album_title:
            cover_url = await _cover_from_discogs(artist_name, album_title)
        lfm_tracks = (info or {}).get("tracks", [])
        deezer_tracks = (deezer or {}).get("tracks", [])
        tracks = deezer_tracks or await get_tracklist_with_fallback(
            lfm_tracks, artist_name, album_title, mb_id=real_mb_id, deezer_id=deezer_id
        )
        tags = (info or {}).get("tags", [])
        itunes_url = (itunes or {}).get("itunes_url", "")
        label = (deezer or {}).get("label") or (itunes or {}).get("label", "")
        genre = (deezer or {}).get("genre") or (itunes or {}).get("genre", "")
        release_type = (itunes or {}).get("release_type", "Album")
        if release_type == "Album" and len(tracks) == 1:
            release_type = "Single"
        listeners = (info or {}).get("listeners", 0)
        playcount = (info or {}).get("playcount", 0)
        deezer_date = (deezer or {}).get("release_date", "")
        itunes_date = (itunes or {}).get("release_date", "")
        release_date = mb_date or deezer_date or itunes_date
        year = release_date[:4] if release_date else requested_year
        platform_links = await get_platform_links(
            artist_name, album_title, itunes_url=itunes_url, mb_id=real_mb_id
        )
        yt_query = urllib.parse.quote(f"{artist_name} {album_title} full album")
        yt_url = f"https://www.youtube.com/results?search_query={yt_query}"

        most_played_track = ""
        if artist_name and tracks:
            global_plays = await lastfm_client.get_artist_top_tracks_global(artist_name)
            if global_plays:
                best = max(tracks, key=lambda t: global_plays.get(t["name"].lower(), 0), default=None)
                if best and global_plays.get(best["name"].lower(), 0) > 0:
                    most_played_track = best["name"]

    result = await db.execute(
        select(AlbumRequest).where(AlbumRequest.musicbrainz_id == real_mb_id)
    )
    existing_request = result.scalar_one_or_none()

    collection_match = _find_collection_album_with_cleanup(
        artist_name,
        album_title,
        collection,
        year=requested_year or year,
        mb_id=real_mb_id,
        cover_url=cover_url or "",
    ) or initial_collection_match
    in_collection = bool(collection_match)
    if collection_match:
        artist_name = collection_match.get("artist") or artist_name
        album_title = collection_match.get("album") or album_title
        if requested_year and str(year or "")[:4] != requested_year:
            year = requested_year
            release_date = release_date if str(release_date or "").startswith(requested_year) else requested_year
        cover_url = collection_match.get("cover_url") or cover_url
        nav_tracks = await get_album_tracks(collection_match.get("nav_id", ""))
        if nav_tracks:
            tracks = nav_tracks

    # Save to permanent cache after local-collection corrections are applied.
    if not cached and artist_name and album_title and tracks:
        await save_album_cache(artist_name, album_title, {
            "artist": artist_name, "album": album_title,
            "mb_id": real_mb_id,
            "year": year, "release_date": release_date,
            "cover_url": cover_url, "tracks": tracks, "tags": tags,
            "label": label, "genre": genre, "listeners": listeners,
            "playcount": playcount, "release_type": release_type,
            "platform_links": platform_links, "yt_url": yt_url,
            "most_played_track": most_played_track,
        })

    try:
        track_stats = await asyncio.wait_for(
            lastfm_client.get_track_global_stats(artist_name, tracks),
            timeout=3.5,
        )
    except Exception:
        track_stats = {}
    tracks, track_popularity_max = _attach_global_track_popularity(tracks, track_stats)

    return templates.TemplateResponse("album_detail.html", {
        "request": request,
        "mb_id": real_mb_id,
        "real_mb_id": real_mb_id,
        "artist": artist_name,
        "album": album_title,
        "year": year,
        "release_date": release_date,
        "cover_url": cover_url,
        "yt_url": yt_url,
        "tracks": tracks,
        "tags": tags,
        "label": label,
        "track_count": len(tracks),
        "genre": genre,
        "listeners": listeners,
        "playcount": playcount,
        "release_type": release_type,
        "platform_links": platform_links,
        "releases": [],
        "release_group": {},
        "existing_request": existing_request,
        "in_collection": in_collection,
        "highlight_track": highlight,
        "most_played_track": most_played_track,
        "track_popularity_max": track_popularity_max,
    })
