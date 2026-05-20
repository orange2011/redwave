from __future__ import annotations

import httpx

from app.config import settings


class QBittorrentError(RuntimeError):
    """Raised when qBittorrent rejects an API request."""


def qbt_base_url() -> str:
    host = (settings.qbt_host or "").strip()
    if not host:
        raise QBittorrentError("No qBittorrent Host URL configured.")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host.rstrip("/")


def _qbt_headers(base_url: str) -> dict[str, str]:
    return {
        "User-Agent": "Redwave/1.0",
        "Origin": base_url,
        "Referer": f"{base_url}/",
    }


def _body_preview(response: httpx.Response) -> str:
    text = (response.text or "").strip()
    if not text:
        return ""
    return text[:160]


def qbt_login_error_message(response: httpx.Response) -> str:
    body = _body_preview(response)
    suffix = f": {body}" if body else ""
    if response.status_code in {401, 403}:
        return (
            f"Login failed (HTTP {response.status_code}{suffix}). "
            "Check qBittorrent username/password. If Redwave runs in Docker, "
            "the Host URL must be reachable from the Redwave container, not only from your browser."
        )
    if response.status_code == 404:
        return (
            "qBittorrent API not found (HTTP 404). If qBittorrent is behind a reverse proxy path, "
            "include that path in Host URL, for example http://server/qbt."
        )
    if response.status_code >= 500:
        return f"qBittorrent server error (HTTP {response.status_code}{suffix})."
    return f"Login failed (HTTP {response.status_code}{suffix})."


class QBittorrentClient:
    async def login(self, client: httpx.AsyncClient, base_url: str) -> None:
        response = await client.post(f"{base_url}/api/v2/auth/login", data={
            "username": settings.qbt_username,
            "password": settings.qbt_password,
        })
        if response.text == "Ok.":
            return

        # Some qBittorrent setups bypass WebUI auth for trusted clients. If that is
        # active, the login endpoint can be unhelpful while authenticated API calls
        # still work, so verify with a harmless endpoint before failing.
        probe = await client.get(f"{base_url}/api/v2/app/version")
        if probe.status_code == 200 and probe.text.strip():
            return

        raise QBittorrentError(qbt_login_error_message(response))

    async def add_torrent(self, torrent_bytes: bytes, tags: list[str] | None = None) -> bool:
        base_url = qbt_base_url()
        data = {"category": settings.qbt_category}
        clean_tags = [tag.strip() for tag in (tags or []) if tag and tag.strip()]
        if clean_tags:
            data["tags"] = ",".join(clean_tags)
        async with httpx.AsyncClient(timeout=10.0, headers=_qbt_headers(base_url)) as client:
            await self.login(client, base_url)
            response = await client.post(f"{base_url}/api/v2/torrents/add", files={
                "torrents": ("upload.torrent", torrent_bytes, "application/x-bittorrent"),
            }, data=data)
        if response.text == "Ok.":
            return True
        raise QBittorrentError(f"Torrent add failed (HTTP {response.status_code}: {_body_preview(response) or 'empty response'}).")

    async def get_torrent_status(self, infohash: str) -> str | None:
        base_url = qbt_base_url()
        async with httpx.AsyncClient(timeout=10.0, headers=_qbt_headers(base_url)) as client:
            await self.login(client, base_url)
            response = await client.get(f"{base_url}/api/v2/torrents/info", params={"hashes": infohash})
        response.raise_for_status()
        torrents = response.json()
        if not torrents:
            return None
        state = torrents[0].get("state", "")
        completed_states = {"uploading", "pausedUP", "stoppedUP", "seeding", "forcedUP"}
        if state in completed_states:
            return "completed"
        return "downloading"

    async def get_all_torrents(self, category: str = "") -> list[dict]:
        base_url = qbt_base_url()
        params = {}
        if category:
            params["category"] = category
        async with httpx.AsyncClient(timeout=10.0, headers=_qbt_headers(base_url)) as client:
            await self.login(client, base_url)
            response = await client.get(f"{base_url}/api/v2/torrents/info", params=params)
        response.raise_for_status()
        return response.json()


qbt_client = QBittorrentClient()
