import html
import httpx
from app.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    return _client


async def get_top_albums(period: str = "week", limit: int = 10) -> list[dict]:
    """Fetch top torrents from RED and return as album dicts."""
    if not settings.red_api_key:
        return []
    try:
        r = await _get_client().get(
            "https://redacted.sh/ajax.php",
            params={"action": "top10", "type": "torrents", "limit": limit, "way": period},
            headers={"Authorization": settings.red_api_key},
        )
        data = r.json()
        if data.get("status") != "success":
            return []

        results = []
        seen = set()
        for group in data.get("response", [{}])[0].get("results", []):
            artist = html.unescape(group.get("artist", ""))
            album = html.unescape(group.get("groupName", ""))
            if not artist or not album:
                continue
            key = f"{artist.lower()}|{album.lower()}"
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "artist": artist,
                "album": album,
                "cover_url": group.get("wikiImage") or group.get("image") or None,
                "year": str(group.get("groupYear", "")),
                "mb_id": "",
                "snatched": group.get("totalSnatched", 0),
            })
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []
