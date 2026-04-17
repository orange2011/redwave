import httpx
from app.config import settings


class QBittorrentClient:
    def __init__(self):
        self._client = httpx.AsyncClient(base_url=settings.qbt_host, timeout=10.0)

    async def login(self):
        await self._client.post("/api/v2/auth/login", data={
            "username": settings.qbt_username,
            "password": settings.qbt_password,
        })

    async def add_torrent(self, torrent_bytes: bytes) -> bool:
        await self.login()
        r = await self._client.post("/api/v2/torrents/add", files={
            "torrents": ("upload.torrent", torrent_bytes, "application/x-bittorrent"),
        }, data={
            "category": settings.qbt_category,
        })
        return r.text == "Ok."

    async def get_torrent_status(self, infohash: str) -> str | None:
        await self.login()
        r = await self._client.get("/api/v2/torrents/info", params={"hashes": infohash})
        torrents = r.json()
        if not torrents:
            return None
        state = torrents[0].get("state", "")
        completed_states = {"uploading", "pausedUP", "stoppedUP", "seeding", "forcedUP"}
        if state in completed_states:
            return "completed"
        return "downloading"

    async def get_all_torrents(self, category: str = "") -> list[dict]:
        await self.login()
        params = {}
        if category:
            params["category"] = category
        r = await self._client.get("/api/v2/torrents/info", params=params)
        return r.json()


qbt_client = QBittorrentClient()
