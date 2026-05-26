import json

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.request import AlbumRequest, TorrentOption
from app.services.qbittorrent import qbt_client
from app.services.redacted import ops_client
from app.services.torrent_meta import TorrentManifest, compare_torrent_payloads, manifests_payload_exact, parse_torrent_manifest


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
OPS_CROSS_SEED_MATCH_POLICY = "payload-map-v2"
SUPPORTED_OPS_CROSS_SEED_POLICIES = {"strict-exact-v1", OPS_CROSS_SEED_MATCH_POLICY}


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


def _cross_seed_save_path(completed_torrent: dict | None, manifest: TorrentManifest | None, rename_map: dict | None = None) -> str:
    if not completed_torrent or not manifest:
        return ""
    if rename_map:
        return (completed_torrent.get("save_path") or "").strip()
    if manifest.file_count > 1:
        return (completed_torrent.get("content_path") or "").strip()
    return (completed_torrent.get("save_path") or "").strip()


def _cross_seed_content_layout(manifest: TorrentManifest | None, rename_map: dict | None = None) -> str:
    if rename_map:
        return "Original"
    if manifest and manifest.file_count > 1:
        return "NoSubfolder"
    return "Original"


async def _apply_ops_cross_seed_mapping(
    torrent_hash: str,
    red_manifest: TorrentManifest,
    ops_manifest: TorrentManifest,
    rename_map: dict[str, str],
) -> None:
    if not torrent_hash:
        return
    if red_manifest.file_count > 1 and red_manifest.name and ops_manifest.name:
        await qbt_client.rename_torrent(torrent_hash, red_manifest.name)
        await qbt_client.rename_folder(torrent_hash, ops_manifest.name, red_manifest.name)
    elif red_manifest.name:
        await qbt_client.rename_torrent(torrent_hash, red_manifest.name)

    for old_path, new_path in rename_map.items():
        if not old_path or not new_path or old_path == new_path:
            continue
        if red_manifest.file_count > 1 and red_manifest.name:
            old_path = f"{red_manifest.name}/{old_path}"
            new_path = f"{red_manifest.name}/{new_path}"
        await qbt_client.rename_file(torrent_hash, old_path, new_path)


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
    if ops.get("match_policy") not in SUPPORTED_OPS_CROSS_SEED_POLICIES:
        ops["status"] = "skipped"
        ops["error"] = "cross-seed was queued before payload match validation"
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
        payload_match = compare_torrent_payloads(red_manifest, ops_manifest)
        if not (payload_match.compatible and manifests_payload_exact(queued_manifest, ops_manifest)):
            ops["status"] = "skipped"
            ops["error"] = "OPS torrent payload no longer matches the selected RED torrent"
        else:
            rename_map = ops.get("rename_map") or {}
            if not isinstance(rename_map, dict):
                rename_map = {}
            expected_rename_map = payload_match.rename_map or {}
            if rename_map != expected_rename_map:
                ops["status"] = "skipped"
                ops["error"] = "OPS torrent mapping no longer matches the selected RED torrent"
                payload.setdefault("cross_seed", {})["ops"] = ops
                option.raw_json = json.dumps(payload)
                return False

            save_path = _cross_seed_save_path(completed_torrent, red_manifest, rename_map)
            if not save_path:
                ops["status"] = "failed"
                ops["error"] = "missing completed RED content path for cross-seed"
            else:
                add_result = await qbt_client.add_torrent_with_result(
                    torrent_bytes,
                    tags=[settings.qbt_ops_tag],
                    save_path=save_path,
                    content_layout=_cross_seed_content_layout(red_manifest, rename_map),
                    skip_checking=False,
                    paused=bool(rename_map),
                )
                ops["status"] = "added" if add_result else "failed"
                if add_result:
                    qbt_hash = add_result.hashes[0] if add_result.hashes else ops_manifest.info_hash
                    if qbt_hash:
                        ops["qbt_hash"] = qbt_hash
                    if rename_map and qbt_hash and red_manifest:
                        await _apply_ops_cross_seed_mapping(qbt_hash, red_manifest, ops_manifest, rename_map)
                        await qbt_client.recheck_torrent(qbt_hash)
                        await qbt_client.resume_torrent(qbt_hash)
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
