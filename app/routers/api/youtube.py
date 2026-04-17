import re
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api")

# In-memory cache: "artist|track" -> video_id or None
_cache: dict[str, str | None] = {}

_YT_ID_RE = re.compile(r'"videoId":"([a-zA-Z0-9_-]{11})"')

_client = httpx.AsyncClient(
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    },
    timeout=8.0,
    follow_redirects=True,
)


async def _search_youtube(query: str) -> str | None:
    try:
        r = await _client.get(
            "https://www.youtube.com/results",
            params={"search_query": query},
        )
        # Extract first video ID from embedded JSON
        m = _YT_ID_RE.search(r.text)
        return m.group(1) if m else None
    except Exception:
        return None


@router.get("/youtube/search")
async def youtube_search(artist: str = "", track: str = ""):
    key = f"{artist.lower()}|{track.lower()}"
    if key not in _cache:
        query = f"{artist} - {track} official audio"
        video_id = await _search_youtube(query)
        _cache[key] = video_id
    video_id = _cache[key]
    if video_id:
        return {"video_id": video_id}
    return JSONResponse({"error": "not found"}, status_code=404)
