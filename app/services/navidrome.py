import httpx
from datetime import datetime, timedelta
from app.config import settings

_collection_cache: dict = {"data": None, "expires": datetime.min}
_COLLECTION_TTL = timedelta(seconds=60)


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


def _format_duration(seconds: int | str | None) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    return f"{total // 60}:{total % 60:02d}"


def _normalize_song(song: dict) -> dict:
    cover_art = song.get("coverArt", "")
    return {
        "track": song.get("title", ""),
        "artist": song.get("artist", ""),
        "album": song.get("album", ""),
        "mb_id": song.get("musicBrainzAlbumId") or song.get("musicBrainzReleaseGroupId") or "",
        "cover_url": f"/api/navidrome/cover/{cover_art}" if cover_art else "",
        "duration": _format_duration(song.get("duration")),
        "nav_id": song.get("id", ""),
        "in_collection": True,
        "source": "navidrome",
    }


def _normalize_album_track(song: dict, index: int) -> dict:
    track_no = song.get("track") or song.get("trackNumber") or index
    return {
        "rank": track_no,
        "name": song.get("title", ""),
        "duration": _format_duration(song.get("duration")),
        "preview": "",
    }


def _normalize_artist(artist: dict) -> dict:
    return {
        "name": artist.get("name", ""),
        "listeners": 0,
        "mb_id": artist.get("musicBrainzId", ""),
        "image": "",
        "source": "navidrome",
        "in_collection": True,
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
    """Return all albums from Navidrome with a short cache for automatic refresh."""
    global _collection_cache
    now = datetime.now()
    if _collection_cache["data"] is not None and now < _collection_cache["expires"]:
        return _collection_cache["data"]
    albums = await _fetch_all_albums()
    normalized = [_normalize(a) for a in albums]
    _collection_cache = {"data": normalized, "expires": now + _COLLECTION_TTL}
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


async def search_library(
    query: str,
    artist_count: int = 5,
    album_count: int = 12,
    song_count: int = 12,
) -> dict:
    url = _base()
    if not url or not settings.navidrome_user or not query.strip():
        return {"artists": [], "albums": [], "tracks": []}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{url}/rest/search3",
                params=_params(
                    query=query,
                    artistCount=artist_count,
                    albumCount=album_count,
                    songCount=song_count,
                ),
            )
            data = r.json().get("subsonic-response", {})
            if data.get("status") != "ok":
                return {"artists": [], "albums": [], "tracks": []}
            result = data.get("searchResult3", {})
            albums = [{**_normalize(a), "in_collection": True, "source": "navidrome"} for a in result.get("album", [])]
            tracks = [_normalize_song(s) for s in result.get("song", [])]
            artists = [_normalize_artist(a) for a in result.get("artist", [])]
            return {"artists": artists, "albums": albums, "tracks": tracks}
    except Exception:
        return {"artists": [], "albums": [], "tracks": []}


async def get_album_tracks(album_id: str) -> list[dict]:
    url = _base()
    if not url or not settings.navidrome_user or not album_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{url}/rest/getAlbum",
                params=_params(id=album_id),
            )
            data = r.json().get("subsonic-response", {})
            if data.get("status") != "ok":
                return []
            songs = data.get("album", {}).get("song", [])
            return [_normalize_album_track(song, index) for index, song in enumerate(songs, start=1)]
    except Exception:
        return []


async def get_random_songs(limit: int = 20) -> list[dict]:
    url = _base()
    if not url or not settings.navidrome_user:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{url}/rest/getRandomSongs",
                params=_params(size=limit),
            )
            data = r.json().get("subsonic-response", {})
            if data.get("status") != "ok":
                return []
            return [_normalize_song(s) for s in data.get("randomSongs", {}).get("song", [])]
    except Exception:
        return []


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
