import httpx
from app.config import settings


def _headers() -> dict:
    return {"X-Api-Key": settings.lidarr_api_key}


def _base() -> str:
    return settings.lidarr_url.rstrip("/")


async def add_artist_unmonitored(artist_name: str) -> bool:
    """Look up an artist on Lidarr and add them unmonitored if not already present."""
    base = _base()
    if not base or not settings.lidarr_api_key:
        return False

    async with httpx.AsyncClient(timeout=15) as client:
        # Check if already in library
        existing = await client.get(f"{base}/api/v1/artist", headers=_headers())
        if existing.status_code == 200:
            for a in existing.json():
                if a.get("artistName", "").lower() == artist_name.lower():
                    return True  # already there

        # Look up on MusicBrainz via Lidarr
        lookup = await client.get(
            f"{base}/api/v1/artist/lookup",
            params={"term": artist_name},
            headers=_headers(),
        )
        if lookup.status_code != 200 or not lookup.json():
            return False

        candidate = lookup.json()[0]

        # Get root folder
        folders = await client.get(f"{base}/api/v1/rootfolder", headers=_headers())
        if folders.status_code != 200 or not folders.json():
            return False
        root_path = folders.json()[0]["path"]

        # Get quality profile
        profiles = await client.get(f"{base}/api/v1/qualityprofile", headers=_headers())
        if profiles.status_code != 200 or not profiles.json():
            return False
        quality_profile_id = profiles.json()[0]["id"]

        # Get metadata profile
        meta_profiles = await client.get(f"{base}/api/v1/metadataprofile", headers=_headers())
        meta_profile_id = meta_profiles.json()[0]["id"] if meta_profiles.status_code == 200 and meta_profiles.json() else 1

        payload = {
            **candidate,
            "rootFolderPath": root_path,
            "qualityProfileId": quality_profile_id,
            "metadataProfileId": meta_profile_id,
            "monitored": False,
            "monitorNewItems": "none",
            "addOptions": {
                "monitor": "none",
                "searchForMissingAlbums": False,
            },
        }

        r = await client.post(f"{base}/api/v1/artist", json=payload, headers=_headers())
        return r.status_code in (200, 201)
