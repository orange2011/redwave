import httpx
from datetime import datetime, timedelta
from app.config import settings

_collection_cache: dict = {"data": None, "expires": datetime.min}


def _params(**extra) -> dict:
    return {
        "u": settings.navidrome_user,
        "p": settings.navidrome_pass,
        "v": "1.16.0",
        "c": "Redwave",
        "f": "json",
        **extra,
    }


def _base() -> str:
    return settings.navidrome_url.rstrip("/")


def _normalize(a: dict) -> dict:
    cover_art = a.get("coverArt", "")
    created = a.get("created", "")
    added_at = 0.0
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            added_at = dt.timestamp()
        except Exception:
            pass
    return {
        "artist": a.get("artist", ""),
        "album": a.get("name", ""),
        "year": str(a.get("year", "")) if a.get("year") else "",
        "cover_url": f"/api/navidrome/cover/{cover_art}" if cover_art else "",
        "mb_id": a.get("musicBrainzId", ""),
        "nav_id": a.get("id", ""),
        "song_count": a.get("songCount", 0),
        "duration": a.get("duration", 0),
        "added_at": added_at,
    }


async def _fetch_all_albums() -> list[dict]:
    url = _base()
    if not url or not settings.navidrome_user:
        return []
    albums = []
    offset = 0
    size = 500
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r = await client.get(
                f"{url}/rest/getAlbumList2",
                params=_params(type="alphabeticalByName", size=size, offset=offset),
            )
            data = r.json().get("subsonic-response", {})
            if data.get("status") != "ok":
                break
            batch = data.get("albumList2", {}).get("album", [])
            if not batch:
                break
            albums.extend(batch)
            if len(batch) < size:
                break
            offset += size
    return albums


async def get_collection() -> list[dict]:
    """Return all albums from Navidrome, cached for 1 hour."""
    global _collection_cache
    now = datetime.now()
    if _collection_cache["data"] is not None and now < _collection_cache["expires"]:
        return _collection_cache["data"]
    albums = await _fetch_all_albums()
    normalized = [_normalize(a) for a in albums]
    _collection_cache = {"data": normalized, "expires": now + timedelta(hours=1)}
    return normalized


async def refresh_collection() -> list[dict]:
    global _collection_cache
    _collection_cache = {"data": None, "expires": datetime.min}
    return await get_collection()


async def get_newest_albums(limit: int = 12) -> list[dict]:
    url = _base()
    if not url or not settings.navidrome_user:
        return []
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{url}/rest/getAlbumList2",
            params=_params(type="newest", size=limit),
        )
        data = r.json().get("subsonic-response", {})
        if data.get("status") != "ok":
            return []
        albums = data.get("albumList2", {}).get("album", [])
        return [_normalize(a) for a in albums]


async def get_cover_bytes(cover_art_id: str) -> tuple[bytes, str] | None:
    """Fetch cover art bytes from Navidrome. Returns (data, mime_type) or None."""
    url = _base()
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{url}/rest/getCoverArt",
                params=_params(id=cover_art_id, size=300),
            )
            if r.status_code == 200:
                ct = r.headers.get("content-type", "image/jpeg")
                return r.content, ct
    except Exception:
        pass
    return None


async def trigger_scan() -> bool:
    url = _base()
    if not url or not settings.navidrome_user:
        return False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{url}/rest/startScan", params=_params())
            data = r.json()
            return data.get("subsonic-response", {}).get("status") == "ok"
    except Exception:
        return False
