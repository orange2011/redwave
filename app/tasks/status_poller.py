import json

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.request import AlbumRequest, TorrentOption
from app.services.qbittorrent import qbt_client
from app.services.redacted import ops_client


COMPLETED_STATES = {
    "uploading",
    "pausedUP",
    "stoppedUP",
    "seeding",
    "forcedUP",
    "queuedUP",
    "stalledUP",
    "checkingUP",
}


def _truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _hash(value: str | None) -> str:
    return (value or "").strip().lower()


def _merge_torrents(*groups: list[dict]) -> list[dict]:
    merged = []
    seen = set()
    for group in groups:
        for torrent in group:
            key = _hash(torrent.get("hash")) or torrent.get("name", "")
            if key in seen:
                continue
            merged.append(torrent)
            seen.add(key)
    return merged


def _find_by_hash(torrents: list[dict], info_hash: str | None) -> dict | None:
    wanted = _hash(info_hash)
    if not wanted:
        return None
    for torrent in torrents:
        if _hash(torrent.get("hash")) == wanted:
            return torrent
    return None


def _is_completed_state(state: str | None) -> bool:
    return (state or "") in COMPLETED_STATES


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

        category_torrents = await qbt_client.get_all_torrents(category=settings.qbt_category)
        all_torrents = category_torrents
        if settings.qbt_category:
            uncategorized_or_other = await qbt_client.get_all_torrents(category="")
            all_torrents = _merge_torrents(category_torrents, uncategorized_or_other)

        for req in active:
            if not req.qbt_hash:
                # Try to match by name
                for t in all_torrents:
                    name = t.get("name", "").lower()
                    if req.artist.lower() in name or req.album.lower() in name:
                        req.qbt_hash = t.get("hash")
                        break

            if req.qbt_hash:
                t = _find_by_hash(all_torrents, req.qbt_hash)
                if t and _is_completed_state(t.get("state")):
                    req.status = "completed"
                    selected = None
                    if req.selected_torrent_id:
                        selected = await db.get(TorrentOption, req.selected_torrent_id)
                    await _add_pending_ops_cross_seed(selected)

        await db.commit()
