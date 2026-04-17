from app.database import AsyncSessionLocal
from app.models.request import AlbumRequest
from app.services.qbittorrent import qbt_client
from sqlalchemy import select


async def poll_active_downloads():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AlbumRequest).where(AlbumRequest.status == "downloading")
        )
        active = result.scalars().all()

        if not active:
            return

        all_torrents = await qbt_client.get_all_torrents(category="music")
        completed_states = {"uploading", "pausedUP", "stoppedUP", "seeding", "forcedUP"}

        for req in active:
            if not req.qbt_hash:
                # Try to match by name
                for t in all_torrents:
                    name = t.get("name", "").lower()
                    if req.artist.lower() in name or req.album.lower() in name:
                        req.qbt_hash = t.get("hash")
                        break

            if req.qbt_hash:
                for t in all_torrents:
                    if t.get("hash") == req.qbt_hash:
                        if t.get("state") in completed_states:
                            req.status = "completed"
                        break

        await db.commit()
