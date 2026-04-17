import asyncio
import time
import httpx
from app.config import settings


class MusicBrainzClient:
    BASE_URL = "https://musicbrainz.org/ws/2"
    _last_call: float = 0.0

    def __init__(self):
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.musicbrainz_user_agent,
                "Accept": "application/json",
            },
            timeout=15.0,
        )

    async def _get(self, path: str, params: dict) -> dict:
        elapsed = time.monotonic() - self._last_call
        if elapsed < 1.1:
            await asyncio.sleep(1.1 - elapsed)
        self._last_call = time.monotonic()
        r = await self._client.get(f"{self.BASE_URL}/{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def search_release_groups(self, query: str, limit: int = 20) -> list[dict]:
        data = await self._get("release-group", {
            "query": query,
            "type": "album|ep|single",
            "fmt": "json",
            "limit": limit,
        })
        return data.get("release-groups", [])

    async def get_release_group(self, mb_id: str) -> dict:
        return await self._get(f"release-group/{mb_id}", {
            "inc": "artists+releases+url-rels",
            "fmt": "json",
        })

    async def get_cover_url(self, mb_id: str) -> str | None:
        try:
            r = await self._client.get(
                f"https://coverartarchive.org/release-group/{mb_id}",
                follow_redirects=True,
            )
            if r.status_code == 200:
                data = r.json()
                images = data.get("images", [])
                for img in images:
                    if img.get("front"):
                        thumbs = img.get("thumbnails", {})
                        return thumbs.get("500") or thumbs.get("250") or img.get("image")
        except Exception:
            pass
        return None


mb_client = MusicBrainzClient()
