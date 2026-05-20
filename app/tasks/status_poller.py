import json

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.request import AlbumRequest, TorrentOption
from app.services.qbittorrent import qbt_client
from app.services.redacted import ops_client


COMPLETED_STATES = {"uploading", "pausedUP", "stoppedUP", "seeding", "forcedUP"}


def _truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _add_pending_ops_cross_seed(option: TorrentOption | None) -> bool:
    if not option or not _truthy(settings.ops_cross_seed):
        return False
    try:
        payload = json.loads(option.raw_json or "{}")
    except json.JSONDecodeError:
        return False

    ops = (payload.get("cross_seed") or {}).get("ops") or {}
    if ops.get("status") != "pending":
        return False

    torrent_id = ops.get("torrent_id")
    if not torrent_id:
        ops["status"] = "failed"
        ops["error"] = "missing torrent id"
        payload.setdefault("cross_seed", {})["ops"] = ops
        option.raw_json = json.dumps(payload)
        return False

    try:
        torrent_bytes = await ops_client.get_torrent_file(int(torrent_id), use_token=False)
        success = await qbt_client.add_torrent(torrent_bytes, tags=[settings.qbt_ops_tag])
        ops["status"] = "added" if success else "failed"
    except Exception as exc:
        ops["status"] = "failed"
        ops["error"] = str(exc)[:200]

    payload.setdefault("cross_seed", {})["ops"] = ops
    option.raw_json = json.dumps(payload)
    return ops["status"] == "added"


async def poll_active_downloads():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AlbumRequest).where(AlbumRequest.status == "downloading")
        )
        active = result.scalars().all()

        if not active:
            return

        all_torrents = await qbt_client.get_all_torrents(category=settings.qbt_category)

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
                        if t.get("state") in COMPLETED_STATES:
                            req.status = "completed"
                            selected = None
                            if req.selected_torrent_id:
                                selected = await db.get(TorrentOption, req.selected_torrent_id)
                            await _add_pending_ops_cross_seed(selected)
                        break

        await db.commit()
