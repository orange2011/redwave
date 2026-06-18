import json

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.request import AlbumRequest, TorrentOption
from app.services.qbittorrent import qbt_client
from app.services.redacted import enabled_tracker_names, tracker_client_for
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
CROSS_SEED_MATCH_POLICY = "payload-map-v2"
OPS_CROSS_SEED_MATCH_POLICY = CROSS_SEED_MATCH_POLICY
SUPPORTED_CROSS_SEED_POLICIES = {"strict-exact-v1", CROSS_SEED_MATCH_POLICY}
SUPPORTED_OPS_CROSS_SEED_POLICIES = SUPPORTED_CROSS_SEED_POLICIES
TRACKER_QBT_TAGS = {
    "red": "qbt_red_tag",
    "ops": "qbt_ops_tag",
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


async def _apply_cross_seed_mapping(
    torrent_hash: str,
    source_manifest: TorrentManifest,
    target_manifest: TorrentManifest,
    rename_map: dict[str, str],
) -> None:
    if not torrent_hash:
        return
    if source_manifest.file_count > 1 and source_manifest.name and target_manifest.name:
        await qbt_client.rename_torrent(torrent_hash, source_manifest.name)
        await qbt_client.rename_folder(torrent_hash, target_manifest.name, source_manifest.name)
    elif source_manifest.name:
        await qbt_client.rename_torrent(torrent_hash, source_manifest.name)

    for old_path, new_path in rename_map.items():
        if not old_path or not new_path or old_path == new_path:
            continue
        if source_manifest.file_count > 1 and source_manifest.name:
            old_path = f"{source_manifest.name}/{old_path}"
            new_path = f"{source_manifest.name}/{new_path}"
        await qbt_client.rename_file(torrent_hash, old_path, new_path)


async def _add_pending_cross_seed(
    option: TorrentOption | None,
    target_tracker: str,
    completed_torrent: dict | None = None,
) -> bool:
    target_tracker = (target_tracker or "").strip().lower()
    if (
        not option
        or target_tracker not in TRACKER_QBT_TAGS
        or target_tracker not in enabled_tracker_names()
        or not _truthy(settings.ops_cross_seed)
    ):
        return False
    try:
        payload = json.loads(option.raw_json or "{}")
    except json.JSONDecodeError:
        return False

    cross_seed = payload.setdefault("cross_seed", {})
    pending = (cross_seed or {}).get(target_tracker) or {}
    if pending.get("status") != "pending":
        return False

    source_tracker = (payload.get("selected") or {}).get("tracker") or payload.get("tracker") or ""
    source_tracker = str(source_tracker).strip().lower()
    source_label = tracker_client_for(source_tracker).label if source_tracker in TRACKER_QBT_TAGS else "selected"
    target_client = tracker_client_for(target_tracker)
    target_label = target_client.label
    if pending.get("match_policy") not in SUPPORTED_CROSS_SEED_POLICIES:
        pending["status"] = "skipped"
        pending["error"] = "cross-seed was queued before payload match validation"
        cross_seed[target_tracker] = pending
        option.raw_json = json.dumps(payload)
        return False

    torrent_id = pending.get("torrent_id")
    if not torrent_id:
        pending["status"] = "failed"
        pending["error"] = "missing torrent id"
        cross_seed[target_tracker] = pending
        option.raw_json = json.dumps(payload)
        return False

    try:
        torrent_bytes = await target_client.get_torrent_file(int(torrent_id), use_token=False)
        target_manifest = parse_torrent_manifest(torrent_bytes)
        source_manifest = TorrentManifest.from_dict((payload.get("selected") or {}).get("torrent_manifest"))
        queued_manifest = TorrentManifest.from_dict(pending.get("torrent_manifest"))
        payload_match = compare_torrent_payloads(source_manifest, target_manifest)
        if not (payload_match.compatible and manifests_payload_exact(queued_manifest, target_manifest)):
            pending["status"] = "skipped"
            pending["error"] = f"{target_label} torrent payload no longer matches the selected {source_label} torrent"
        else:
            rename_map = pending.get("rename_map") or {}
            if not isinstance(rename_map, dict):
                rename_map = {}
            expected_rename_map = payload_match.rename_map or {}
            if rename_map != expected_rename_map:
                pending["status"] = "skipped"
                pending["error"] = f"{target_label} torrent mapping no longer matches the selected {source_label} torrent"
                cross_seed[target_tracker] = pending
                option.raw_json = json.dumps(payload)
                return False

            save_path = _cross_seed_save_path(completed_torrent, source_manifest, rename_map)
            if not save_path:
                pending["status"] = "failed"
                pending["error"] = f"missing completed {source_label} content path for cross-seed"
            else:
                add_result = await qbt_client.add_torrent_with_result(
                    torrent_bytes,
                    tags=[getattr(settings, TRACKER_QBT_TAGS[target_tracker], "")],
                    save_path=save_path,
                    content_layout=_cross_seed_content_layout(source_manifest, rename_map),
                    skip_checking=False,
                    paused=bool(rename_map),
                )
                pending["status"] = "added" if add_result else "failed"
                if add_result:
                    qbt_hash = add_result.hashes[0] if add_result.hashes else target_manifest.info_hash
                    if qbt_hash:
                        pending["qbt_hash"] = qbt_hash
                    if rename_map and qbt_hash and source_manifest:
                        await _apply_cross_seed_mapping(qbt_hash, source_manifest, target_manifest, rename_map)
                        await qbt_client.recheck_torrent(qbt_hash)
                        await qbt_client.resume_torrent(qbt_hash)
    except Exception:
        pending["status"] = "failed"
        pending["error"] = f"{target_label} cross-seed failed; check tracker and qBittorrent settings"

    cross_seed[target_tracker] = pending
    option.raw_json = json.dumps(payload)
    return pending["status"] == "added"


async def _add_pending_ops_cross_seed(option: TorrentOption | None, completed_torrent: dict | None = None) -> bool:
    return await _add_pending_cross_seed(option, "ops", completed_torrent)


async def _add_pending_cross_seeds(option: TorrentOption | None, completed_torrent: dict | None = None) -> bool:
    if not option:
        return False
    try:
        payload = json.loads(option.raw_json or "{}")
    except json.JSONDecodeError:
        return False

    added = False
    cross_seed = payload.get("cross_seed") or {}
    for target_tracker in list(cross_seed.keys()):
        if target_tracker in TRACKER_QBT_TAGS:
            added = await _add_pending_cross_seed(option, target_tracker, completed_torrent) or added
    return added


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
                    await _add_pending_cross_seeds(selected, t)

        await db.commit()
