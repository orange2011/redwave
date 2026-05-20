"""Permanent SQLite cache for album page data."""
import json
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.cache import AlbumCache
from app.utils import normalize_artist


def _base_key(artist: str, album: str) -> str:
    return f"{artist.lower()}|{album.lower()}"


def _key(artist: str, album: str, year: str = "", mb_id: str = "") -> str:
    base = _base_key(artist, album)
    identity = (mb_id or year or "").strip().lower()
    return f"{base}|{identity}" if identity else base


def _matches_identity(data: dict, year: str = "", mb_id: str = "") -> bool:
    wanted_mbid = (mb_id or "").strip()
    wanted_year = str(year or "").strip()[:4]
    if wanted_mbid and (data.get("mb_id") or "").strip() == wanted_mbid:
        return True
    if wanted_year and str(data.get("year") or "").strip()[:4] == wanted_year:
        return True
    return not wanted_mbid and not wanted_year


async def get_cached_album(artist: str, album: str, year: str = "", mb_id: str = "") -> dict | None:
    keys = []
    if mb_id:
        keys.append(_key(artist, album, mb_id=mb_id))
    if year:
        keys.append(_key(artist, album, year=year))
    if not keys:
        keys.append(_base_key(artist, album))

    async with AsyncSessionLocal() as db:
        for key in keys:
            row = await db.get(AlbumCache, key)
            if not row:
                continue
            data = json.loads(row.data_json)
            if _matches_identity(data, year=year, mb_id=mb_id):
                return data
        if year or mb_id:
            row = await db.get(AlbumCache, _base_key(artist, album))
            if row:
                data = json.loads(row.data_json)
                if _matches_identity(data, year=year, mb_id=mb_id):
                    return data
        return None


async def get_cached_albums_for_artist(artist: str, limit: int = 50) -> list[dict]:
    """Return album pages Redwave has already resolved for this artist."""
    wanted = normalize_artist(artist)
    if not wanted:
        return []

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AlbumCache).order_by(AlbumCache.cached_at.desc()).limit(max(limit * 4, limit))
        )
        rows = result.scalars().all()

    albums: list[dict] = []
    for row in rows:
        try:
            data = json.loads(row.data_json)
        except json.JSONDecodeError:
            continue

        row_artist = data.get("artist") or row.artist
        if normalize_artist(row_artist) != wanted:
            continue

        album = data.get("album") or row.album
        if not album:
            continue

        albums.append({
            "artist": row_artist,
            "album": album,
            "year": data.get("year", ""),
            "release_date": data.get("release_date", ""),
            "cover_url": data.get("cover_url", ""),
            "mb_id": data.get("mb_id", ""),
            "release_type": data.get("release_type", "Album"),
        })
        if len(albums) >= limit:
            break

    return albums


async def save_album_cache(artist: str, album: str, data: dict):
    from datetime import datetime
    key = _key(artist, album, year=data.get("year", ""), mb_id=data.get("mb_id", ""))
    async with AsyncSessionLocal() as db:
        row = await db.get(AlbumCache, key)
        if row:
            row.data_json = json.dumps(data)
            row.cached_at = datetime.utcnow()
        else:
            db.add(AlbumCache(
                cache_key=key,
                artist=artist,
                album=album,
                data_json=json.dumps(data),
            ))
        await db.commit()


async def bust_album_cache(artist: str, album: str, year: str = "", mb_id: str = ""):
    key = _key(artist, album, year=year, mb_id=mb_id)
    async with AsyncSessionLocal() as db:
        row = await db.get(AlbumCache, key)
        if row:
            await db.delete(row)
            await db.commit()
