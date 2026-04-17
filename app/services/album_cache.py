"""Permanent SQLite cache for album page data."""
import json
from app.database import AsyncSessionLocal
from app.models.cache import AlbumCache


def _key(artist: str, album: str) -> str:
    return f"{artist.lower()}|{album.lower()}"


async def get_cached_album(artist: str, album: str) -> dict | None:
    key = _key(artist, album)
    async with AsyncSessionLocal() as db:
        row = await db.get(AlbumCache, key)
        if not row:
            return None
        return json.loads(row.data_json)


async def save_album_cache(artist: str, album: str, data: dict):
    from datetime import datetime
    key = _key(artist, album)
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


async def bust_album_cache(artist: str, album: str):
    key = _key(artist, album)
    async with AsyncSessionLocal() as db:
        row = await db.get(AlbumCache, key)
        if row:
            await db.delete(row)
            await db.commit()
