import json

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.request import AlbumRequest, TorrentOption
from app.services.qbittorrent import qbt_client
from app.services.redacted import ops_client
from app.services.torrent_meta import TorrentManifest, manifests_payload_compatible, parse_torrent_manifest


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
OPS_CROSS_SEED_MATCH_POLICY = "strict-exact-v1"


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


def _cross_seed_save_path(completed_torrent: dict | None, manifest: TorrentManifest | None) -> str:
    if not completed_torrent or not manifest:
        return ""
    if manifest.file_count > 1:
        return (completed_torrent.get("content_path") or "").strip()
    return (completed_torrent.get("save_path") or "").strip()


async def _add_pending_ops_cross_seed(option: TorrentOption | None, completed_torrent: dict | None = None) -> bool:
    if not option or not _truthy(settings.ops_cross_seed):
        return False
    try:
        payload = json.loads(option.raw_json or "{}")
    except json.JSONDecodeError:
        return False

    ops = (payload.get("cross_seed") or {}).get("ops") or {}
    if ops.get("status") != "pending":
        return False
    if ops.get("match_policy") != OPS_CROSS_SEED_MATCH_POLICY:
        ops["status"] = "skipped"
        ops["error"] = "cross-seed was queued before strict match validation"
        payload.setdefault("cross_seed", {})["ops"] = ops
        option.raw_json = json.dumps(payload)
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
        ops_manifest = parse_torrent_manifest(torrent_bytes)
        red_manifest = TorrentManifest.from_dict((payload.get("selected") or {}).get("torrent_manifest"))
        queued_manifest = TorrentManifest.from_dict(ops.get("torrent_manifest"))
        if not (
            manifests_payload_compatible(red_manifest, ops_manifest)
            and manifests_payload_compatible(queued_manifest, ops_manifest)
        ):
            ops["status"] = "skipped"
            ops["error"] = "OPS torrent payload no longer matches the selected RED torrent"
        else:
            save_path = _cross_seed_save_path(completed_torrent, red_manifest)
            if not save_path:
                ops["status"] = "failed"
                ops["error"] = "missing completed RED content path for cross-seed"
            else:
                add_result = await qbt_client.add_torrent_with_result(
                    torrent_bytes,
                    tags=[settings.qbt_ops_tag],
                    save_path=save_path,
                    content_layout="NoSubfolder" if red_manifest and red_manifest.file_count > 1 else "Original",
                    skip_checking=False,
                )
                ops["status"] = "added" if add_result else "failed"
                if add_result and add_result.hashes:
                    ops["qbt_hash"] = add_result.hashes[0]
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
                    await _add_pending_ops_cross_seed(selected, t)

        await db.commit()
