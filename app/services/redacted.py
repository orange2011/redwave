import httpx
from app.config import settings


class RedactedClient:
    BASE_URL = "https://redacted.sh/ajax.php"

    def __init__(self):
        self._client = httpx.AsyncClient(
            headers={"Authorization": settings.red_api_key},
            timeout=15.0,
            follow_redirects=False,
        )

    async def search_torrents(self, artist: str, album: str) -> list[dict]:
        r = await self._client.get(self.BASE_URL, params={
            "action": "browse",
            "searchstr": f"{artist} {album}",
            "filter_cat[1]": 1,
        })
        if r.status_code in (301, 302, 303, 307, 308):
            raise ValueError("RED API key invalid or expired — update RED_API_KEY in .env")
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            return []
        return data.get("response", {}).get("results", [])

    async def get_torrent_file(self, torrent_id: int) -> bytes:
        r = await self._client.get(self.BASE_URL, params={
            "action": "download",
            "id": torrent_id,
            "usetoken": 0,
        })
        r.raise_for_status()
        return r.content

    async def get_torrent_info(self, torrent_id: int) -> dict:
        r = await self._client.get(self.BASE_URL, params={
            "action": "torrent",
            "id": torrent_id,
        })
        r.raise_for_status()
        data = r.json()
        return data.get("response", {})


red_client = RedactedClient()
