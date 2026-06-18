import asyncio
import json
import re
import unicodedata
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.services.redacted import (
    current_media_scores,
    enabled_tracker_names,
    media_score_summary,
    normalize_quality_profile,
    normalize_tracker_name,
    normalize_token_mode,
    ops_client,
    ordered_tracker_names,
    quality_profile_label,
    tracker_token_mode,
    token_mode_label,
    tracker_client_for,
    torrent_media_score,
    torrent_preference_sort_key,
    torrent_preference_score,
)
from app.models.request import AlbumRequest, TorrentOption
from app.database import get_db
from app.utils import normalize_album, normalize_artist
from app.config import settings
from app.services.album_cache import get_cached_album
from app.services.lastfm import get_tracklist_with_fallback
from app.services.torrent_meta import TorrentManifest, compare_torrent_payloads, parse_torrent_manifest

router = APIRouter(prefix="/api")


def _age_days(time_str: str) -> int:
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0


def _fmt_size(b: int) -> str:
    if b >= 1_073_741_824:
        return f"{b/1_073_741_824:.1f} GB"
    return f"{b/1_048_576:.0f} MB"


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STRICT_TEXT_RE = re.compile(r"[\w]+", re.UNICODE)
CROSS_SEED_MATCH_POLICY = "payload-map-v2"
OPS_CROSS_SEED_MATCH_POLICY = CROSS_SEED_MATCH_POLICY
TRACKER_CROSS_SEED_TARGET = {
    "red": "ops",
    "ops": "red",
}
_VARIOUS_ARTIST_KEYS = {"various", "various artists", "v a", "va"}


def _match_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    folded = value.encode("ascii", "ignore").decode("ascii")
    value = folded if _TOKEN_RE.search(folded) else unicodedata.normalize("NFKC", value)
    value = value.lower()
    value = value.replace("&", " and ")
    value = normalize_album(value)
    tokens = _TOKEN_RE.findall(value)
    if tokens:
        return " ".join(tokens)
    return " ".join(_STRICT_TEXT_RE.findall(value))


def _tokens(value: str) -> set[str]:
    return set(_match_text(value).split())


def _text_score(wanted: str, found: str, exact_points: int, contains_points: int, token_points: int) -> int:
    wanted_text = _match_text(wanted)
    found_text = _match_text(found)
    if not wanted_text or not found_text:
        return 0
    if wanted_text == found_text:
        return exact_points
    if wanted_text in found_text or found_text in wanted_text:
        return contains_points
    return token_points * len(_tokens(wanted) & _tokens(found))


def _album_match_score(album: str, group_album: str) -> int:
    return _text_score(album, group_album, exact_points=12, contains_points=8, token_points=4)


def _is_various_artist(value: str) -> bool:
    return normalize_artist(value) in _VARIOUS_ARTIST_KEYS


def _artist_matches_for_track_fallback(wanted: str, found: str, artist_confirmed: bool = False) -> bool:
    wanted_artist = normalize_artist(wanted)
    found_artist = normalize_artist(found)
    if artist_confirmed and _is_various_artist(found):
        return bool(wanted_artist)
    return bool(
        wanted_artist
        and found_artist
        and (
            wanted_artist == found_artist
            or wanted_artist in found_artist
            or found_artist in wanted_artist
        )
    )


def _strict_album_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("&", " and ").casefold()
    return " ".join(_STRICT_TEXT_RE.findall(value))


def _release_year(value: str | int | None) -> str:
    return str(value or "").strip()[:4]


def _is_exact_group_match(group: dict, artist: str, album: str, year: str) -> bool:
    wanted_artist = normalize_artist(artist)
    group_artist = normalize_artist(group.get("artist", ""))
    wanted_album = _strict_album_text(album)
    group_album = _strict_album_text(group.get("groupName", ""))
    wanted_year = _release_year(year)
    group_year = _release_year(group.get("groupYear"))

    if wanted_artist and group_artist and wanted_artist != group_artist:
        return False
    if wanted_album and group_album and wanted_album != group_album:
        return False
    if wanted_year and group_year and wanted_year != group_year:
        return False
    return bool(wanted_album and group_album)


def _group_match_score(group: dict, artist: str, album: str, year: str) -> int:
    group_artist = group.get("artist", "")
    group_album = group.get("groupName", "")
    group_year = str(group.get("groupYear") or "")

    score = 0
    score += _text_score(artist, group_artist, exact_points=8, contains_points=5, token_points=3)
    score += _album_match_score(album, group_album)
    if year and group_year == str(year):
        score += 3

    return score


def _is_freeleech(torrent: dict) -> bool:
    return bool(
        torrent.get("freeTorrent")
        or torrent.get("isFreeleech")
        or torrent.get("isFreeLeech")
        or torrent.get("isFreeload")
        or torrent.get("isNeutralLeech")
        or torrent.get("isPersonalFreeleech")
        or torrent.get("isPersonalFreeLeech")
    )


def _truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_torrent_rows(
    groups: list[dict],
    artist: str,
    album: str,
    year: str,
    token_mode: str | dict[str, str],
    quality_profile: str,
    media_scores: dict[str, int],
) -> list[dict]:
    torrent_list = []
    for group in groups:
        group_id = group.get("groupId")
        tracker = group.get("_redwave_tracker", "red")
        row_token_mode = (
            normalize_token_mode(token_mode.get(tracker))
            if isinstance(token_mode, dict)
            else normalize_token_mode(token_mode)
        )
        tracker_label = group.get("_redwave_tracker_label", "RED" if tracker == "red" else "OPS")
        group_url = group.get("_redwave_group_url") or tracker_client_for(tracker).group_url(group_id)
        g_artist = group.get("artist", artist)
        g_album = group.get("groupName", album)
        g_year = group.get("groupYear", year)
        track_hits = group.get("_redwave_track_hits") or []
        track_artist_confirmed = bool(group.get("_redwave_track_artist_confirmed"))
        is_track_fallback = bool(group.get("_redwave_search_mode") == "track_fallback" and track_hits)
        artist_match_score = _text_score(artist, g_artist, exact_points=8, contains_points=5, token_points=3)
        album_score = _album_match_score(album, g_album)
        match_score = _group_match_score(group, artist, album, year)
        match_exact = _is_exact_group_match(group, artist, album, year)
        year_mismatch = bool(year and g_year and str(g_year) != str(year))
        if is_track_fallback:
            if artist and not _artist_matches_for_track_fallback(artist, g_artist, track_artist_confirmed):
                continue
            track_match_score = min(48, 4 * len(track_hits))
            if year and str(g_year) == str(year):
                track_match_score += 3
            match_score = max(match_score, artist_match_score + track_match_score)
            match_exact = False
        else:
            if album and album_score <= 0:
                continue
            if year_mismatch and album_score < 12:
                continue
            if match_score < 8:
                continue

        for t in group.get("torrents", []):
            fmt = t.get("format", "")
            encoding = t.get("encoding", "")
            media = t.get("media", "")
            remaster = t.get("remasterTitle", "")
            has_log = t.get("hasLog", False)
            log_score = t.get("logScore", 0)
            has_cue = t.get("hasCue", False)
            free = _is_freeleech(t)
            can_use_token = bool(t.get("canUseToken"))
            will_use_token = row_token_mode in ("preferred", "required") and can_use_token and not free
            if row_token_mode == "required" and not can_use_token and not free:
                continue
            seeders = t.get("seeders", 0)
            leechers = t.get("leechers", 0)
            size = t.get("size", 0)
            age = _age_days(t.get("time", ""))

            parts = [f"{g_artist} - {g_album}"]
            if g_year:
                parts.append(f"[{g_year}]")
            if remaster:
                parts.append(f"[{remaster}]")
            label = f"{fmt} {encoding}".strip()
            if media:
                label += f" / {media}"
            if has_log:
                label += f" / Log ({log_score}%)"
            if has_cue:
                label += " / Cue"
            parts.append(f"[{label}]")
            title = " ".join(parts)
            track_help = ""
            if is_track_fallback:
                preview_hits = ", ".join(track_hits[:3])
                if len(track_hits) > 3:
                    preview_hits += f", +{len(track_hits) - 3} more"
                track_help = f"Matched by album track titles: {preview_hits}"

            torrent_id = t.get("torrentId")
            torrent_list.append({
                "tracker": tracker,
                "tracker_label": tracker_label,
                "tracker_url": group_url,
                "torrent_id": torrent_id,
                "group_id": group_id,
                "red_torrent_id": torrent_id,
                "red_group_id": group_id,
                "title": title,
                "format": fmt,
                "encoding": encoding,
                "media": media,
                "remaster": remaster,
                "size_bytes": size,
                "size_human": _fmt_size(size),
                "seeders": seeders,
                "leechers": leechers,
                "age_days": age,
                "freeleech": free,
                "can_use_token": can_use_token,
                "will_use_token": will_use_token,
                "token_mode": row_token_mode,
                "quality_profile": quality_profile,
                "media_score": torrent_media_score(t, media_scores),
                "preference_score": torrent_preference_score(t, quality_profile, media_scores),
                "has_log": has_log,
                "log_score": log_score,
                "has_cue": has_cue,
                "uploader": t.get("username", ""),
                "match_score": match_score,
                "match_exact": match_exact,
                "match_label": "Track" if is_track_fallback else ("Exact" if match_exact else "Close"),
                "match_source": "track" if is_track_fallback else ("exact" if match_exact else "close"),
                "match_help": track_help,
                "track_hit_count": len(track_hits),
            })

    return _sort_torrent_rows(torrent_list, quality_profile, media_scores)


def _sort_torrent_rows(
    torrent_list: list[dict],
    quality_profile: str,
    media_scores: dict[str, int],
    primary_tracker: str | None = None,
) -> list[dict]:
    primary_tracker = normalize_tracker_name(primary_tracker or settings.primary_tracker)
    torrent_list.sort(key=lambda x: (
        -x["match_score"],
        0 if x.get("tracker") == primary_tracker else 1,
        torrent_preference_sort_key(x, quality_profile, media_scores),
        -x["seeders"],
    ))
    return torrent_list


def _source_note(
    red_count: int,
    ops_count: int,
    ops_configured: bool,
    track_fallback_count: int = 0,
    active_trackers: list[str] | None = None,
) -> str:
    active_trackers = active_trackers or ["red", "ops"]
    if red_count and ops_count:
        note = f"Showing RED and OPS results ({red_count} RED, {ops_count} OPS)."
    elif red_count:
        note = "Showing RED results."
        if "ops" in active_trackers and not ops_configured:
            note += " Add an OPS API key in Settings to compare both trackers."
    elif ops_count:
        note = "Showing OPS results."
    else:
        note = ""

    if track_fallback_count:
        suffix = (
            f"Included {track_fallback_count} result"
            f"{'' if track_fallback_count == 1 else 's'} matched by album track titles."
        )
        return f"{note} {suffix}".strip()
    return note


async def _album_tracks_for_tracker_fallback(
    artist: str,
    album: str,
    search_album: str,
    year: str,
    mb_id: str,
) -> list[dict]:
    cached = await get_cached_album(artist, album, year=year, mb_id=mb_id)
    if not cached and search_album != album:
        cached = await get_cached_album(artist, search_album, year=year, mb_id=mb_id)
    tracks = (cached or {}).get("tracks") or []
    if tracks:
        return tracks
    if not artist or not search_album:
        return []
    try:
        return await get_tracklist_with_fallback([], artist, search_album, mb_id=mb_id)
    except Exception:
        return []


def _same_quality_value(wanted: str, found: str) -> bool:
    if not (wanted or "").strip():
        return True
    return _match_text(wanted) == _match_text(found)


def _cross_seed_client_for(target_tracker: str):
    return ops_client if target_tracker == "ops" else tracker_client_for(target_tracker)


async def _find_cross_seed_match(
    target_tracker: str,
    artist: str,
    album: str,
    year: str,
    size_bytes: int,
    token_mode: str,
    quality_profile: str,
    media_scores: dict[str, int],
    selected_format: str = "",
    selected_encoding: str = "",
    selected_media: str = "",
    selected_remaster: str = "",
    selected_manifest: TorrentManifest | None = None,
) -> dict | None:
    target_tracker = normalize_tracker_name(target_tracker)
    target_client = _cross_seed_client_for(target_tracker)
    if (
        target_tracker not in enabled_tracker_names()
        or not _truthy(settings.ops_cross_seed)
        or not target_client.is_configured()
        or size_bytes <= 0
        or not selected_manifest
    ):
        return None

    try:
        target_results = await target_client.search_torrents(artist, normalize_album(album))
    except Exception:
        return None

    rows = _build_torrent_rows(
        target_results,
        artist,
        normalize_album(album),
        year,
        token_mode,
        quality_profile,
        media_scores,
    )
    for row in rows:
        if int(row.get("size_bytes") or 0) != int(size_bytes):
            continue
        if not row.get("match_exact"):
            continue
        if not _same_quality_value(selected_format, row.get("format", "")):
            continue
        if not _same_quality_value(selected_encoding, row.get("encoding", "")):
            continue
        if not _same_quality_value(selected_media, row.get("media", "")):
            continue
        if not _same_quality_value(selected_remaster, row.get("remaster", "")):
            continue
        try:
            target_torrent_bytes = await target_client.get_torrent_file(int(row.get("torrent_id") or 0), use_token=False)
            target_manifest = parse_torrent_manifest(target_torrent_bytes)
        except Exception:
            continue
        payload_match = compare_torrent_payloads(selected_manifest, target_manifest)
        if not payload_match.compatible:
            continue
        row["torrent_manifest"] = target_manifest.to_dict()
        row["payload_match"] = payload_match.to_dict()
        return row
    return None


async def _find_ops_cross_seed_match(
    artist: str,
    album: str,
    year: str,
    size_bytes: int,
    token_mode: str,
    quality_profile: str,
    media_scores: dict[str, int],
    selected_format: str = "",
    selected_encoding: str = "",
    selected_media: str = "",
    selected_remaster: str = "",
    selected_manifest: TorrentManifest | None = None,
) -> dict | None:
    return await _find_cross_seed_match(
        "ops",
        artist,
        album,
        year,
        size_bytes,
        token_mode,
        quality_profile,
        media_scores,
        selected_format=selected_format,
        selected_encoding=selected_encoding,
        selected_media=selected_media,
        selected_remaster=selected_remaster,
        selected_manifest=selected_manifest,
    )


@router.get("/torrents/search", response_class=HTMLResponse)
async def search_torrents(
    request: Request,
    mb_id: str = Query(...),
    artist: str = Query(...),
    album: str = Query(...),
    year: str = Query(default=""),
    cover_url: str = Query(default=""),
    track_fallback: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    search_album = normalize_album(album)
    active_trackers = enabled_tracker_names()
    primary_tracker = normalize_tracker_name(settings.primary_tracker)
    token_modes = {name: tracker_token_mode(name) for name in active_trackers}
    quality_profile = normalize_quality_profile(settings.red_quality_profile)
    media_scores = current_media_scores()
    errors: dict[str, str] = {}

    async def _search_tracker(tracker: str) -> tuple[str, list[dict]]:
        client = tracker_client_for(tracker)
        if not client.is_configured():
            return tracker, []
        try:
            groups = await client.search_torrents(artist, search_album)
            groups.sort(
                key=lambda group: _group_match_score(group, artist, search_album, year),
                reverse=True,
            )
            return tracker, groups
        except ValueError as exc:
            errors[tracker] = str(exc)
            return tracker, []
        except Exception:
            return tracker, []

    search_results = dict(await asyncio.gather(*(_search_tracker(name) for name in active_trackers)))
    rows_by_tracker = {
        name: _build_torrent_rows(
            search_results.get(name, []),
            artist,
            search_album,
            year,
            token_modes,
            quality_profile,
            media_scores,
        )
        for name in active_trackers
    }

    has_rows = any(rows_by_tracker.values())
    can_track_fallback = bool(not has_rows and not errors)
    if track_fallback and not has_rows and not errors:
        tracks = await _album_tracks_for_tracker_fallback(artist, album, search_album, year, mb_id)
        can_track_fallback = False
        if tracks:
            async def _search_tracks(tracker: str) -> tuple[str, list[dict]]:
                client = tracker_client_for(tracker)
                if not client.is_configured():
                    return tracker, []
                try:
                    groups = await client.search_torrents_by_tracks(artist, search_album, tracks)
                    return tracker, _build_torrent_rows(
                        groups,
                        artist,
                        search_album,
                        year,
                        token_modes,
                        quality_profile,
                        media_scores,
                    )
                except ValueError as exc:
                    errors[tracker] = str(exc)
                    return tracker, []
                except Exception:
                    return tracker, []

            rows_by_tracker.update(
                dict(await asyncio.gather(*(_search_tracks(name) for name in active_trackers)))
            )

    torrent_list = _sort_torrent_rows(
        [row for name in active_trackers for row in rows_by_tracker.get(name, [])],
        quality_profile,
        media_scores,
        primary_tracker=primary_tracker,
    )
    red_rows = rows_by_tracker.get("red", [])
    ops_rows = rows_by_tracker.get("ops", [])
    track_fallback_count = sum(1 for row in torrent_list if row.get("match_source") == "track")
    source_note = _source_note(
        len(red_rows),
        len(ops_rows),
        ops_client.is_configured(),
        track_fallback_count,
        active_trackers,
    )
    cross_seed_enabled = (
        "red" in active_trackers
        and "ops" in active_trackers
        and _truthy(settings.ops_cross_seed)
        and tracker_client_for("red").is_configured()
        and ops_client.is_configured()
    )

    configured_active = [name for name in active_trackers if tracker_client_for(name).is_configured()]
    if not configured_active:
        labels = " or ".join(tracker_client_for(name).label for name in active_trackers)
        error = f"No {labels} API key is configured."
    else:
        error = " / ".join(errors.values()) if not torrent_list else ""
    active_tracker_labels = [tracker_client_for(name).label for name in ordered_tracker_names()]
    token_summary = " / ".join(
        f"{tracker_client_for(name).label} {token_mode_label(token_modes[name])}"
        for name in ordered_tracker_names()
    )

    return templates.TemplateResponse("partials/torrent_picker.html", {
        "request": request,
        "torrents": torrent_list,
        "error": error,
        "source_note": source_note,
        "mb_id": mb_id,
        "artist": artist,
        "album": album,
        "year": year,
        "cover_url": cover_url,
        "freeleech_token_label": token_summary,
        "quality_profile": quality_profile,
        "quality_profile_label": quality_profile_label(quality_profile),
        "media_score_summary": media_score_summary(media_scores),
        "cross_seed_enabled": cross_seed_enabled,
        "ops_cross_seed_enabled": cross_seed_enabled,
        "can_track_fallback": can_track_fallback and not track_fallback,
        "track_fallback_active": track_fallback,
        "active_tracker_labels": active_tracker_labels,
    })


@router.post("/torrents/grab", response_class=HTMLResponse)
async def grab_torrent(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.services.qbittorrent import qbt_client

    form = await request.form()
    mb_id = form.get("mb_id", "")
    artist = form.get("artist", "")
    album = form.get("album", "")
    year = form.get("year", "")
    cover_url = form.get("cover_url", "")
    red_torrent_id = int(form.get("red_torrent_id", 0))
    red_group_id = int(form.get("red_group_id", 0))
    fmt = form.get("format", "")
    encoding = form.get("encoding", "")
    size_bytes = int(form.get("size_bytes", 0))
    seeders = int(form.get("seeders", 0))
    use_freeleech_token = form.get("use_freeleech_token", "0") == "1"
    freeleech_token_mode = normalize_token_mode(form.get("freeleech_token_mode", ""))
    tracker = str(form.get("tracker", "red")).strip().lower()
    if tracker not in {"red", "ops"}:
        tracker = "red"
    tracker_client = tracker_client_for(tracker)
    media = form.get("media", "")
    remaster = form.get("remaster", "")
    quality_profile = normalize_quality_profile(settings.red_quality_profile)
    media_scores = current_media_scores()
    cross_seed_status = ""
    cross_seed_target_label = ""
    grab_error = ""

    # Create or get existing request
    result = await db.execute(
        select(AlbumRequest).where(AlbumRequest.musicbrainz_id == mb_id)
    )
    album_request = result.scalar_one_or_none()
    if not album_request:
        album_request = AlbumRequest(
            musicbrainz_id=mb_id,
            artist=artist,
            album=album,
            year=year,
            cover_url=cover_url,
            status="downloading",
        )
        db.add(album_request)
        await db.flush()
    else:
        album_request.status = "downloading"

    # Download torrent file and send to qBittorrent
    raw_json = {"tracker": tracker}
    try:
        if tracker not in enabled_tracker_names():
            raise ValueError(f"{tracker_client.label} is disabled in Tracker Search Mode.")
        if not tracker_client.is_configured():
            raise ValueError(f"{tracker_client.label} API key is not configured.")
        torrent_bytes = await tracker_client.get_torrent_file(
            red_torrent_id,
            use_token=use_freeleech_token,
            token_mode=freeleech_token_mode,
        )
        selected_manifest = parse_torrent_manifest(torrent_bytes)
        qbt_tag = settings.qbt_red_tag if tracker == "red" else settings.qbt_ops_tag
        add_result = await qbt_client.add_torrent_with_result(torrent_bytes, tags=[qbt_tag])
        if add_result:
            album_request.status = "downloading"
            if add_result.hashes:
                album_request.qbt_hash = add_result.hashes[0]
                raw_json["qbt_hash"] = add_result.hashes[0]
            target_tracker = TRACKER_CROSS_SEED_TARGET.get(tracker, "")
            if (
                target_tracker
                and target_tracker in enabled_tracker_names()
                and _truthy(settings.ops_cross_seed)
                and tracker_client_for(target_tracker).is_configured()
            ):
                target_client = tracker_client_for(target_tracker)
                cross_seed_target_label = target_client.label
                cross_seed_match = await _find_cross_seed_match(
                    target_tracker,
                    artist,
                    album,
                    year,
                    size_bytes,
                    "never",
                    quality_profile,
                    media_scores,
                    selected_format=fmt,
                    selected_encoding=encoding,
                    selected_media=media,
                    selected_remaster=remaster,
                    selected_manifest=selected_manifest,
                )
                if cross_seed_match:
                    raw_json["cross_seed"] = {
                        target_tracker: {
                            "status": "pending",
                            "torrent_id": cross_seed_match.get("torrent_id"),
                            "group_id": cross_seed_match.get("group_id"),
                            "title": cross_seed_match.get("title", ""),
                            "size_bytes": cross_seed_match.get("size_bytes", 0),
                            "match_policy": CROSS_SEED_MATCH_POLICY,
                            "match_mode": (cross_seed_match.get("payload_match") or {}).get("match_mode", "exact"),
                            "rename_map": (cross_seed_match.get("payload_match") or {}).get("rename_map", {}),
                            "torrent_manifest": cross_seed_match.get("torrent_manifest"),
                        }
                    }
                    cross_seed_status = "queued"
                else:
                    raw_json["cross_seed"] = {target_tracker: {"status": "no_match"}}
                    cross_seed_status = "no_match"
        else:
            album_request.status = "failed"
    except Exception as e:
        album_request.status = "failed"
        message = str(e)
        if isinstance(e, ValueError) and (
            message.startswith(("RED ", "OPS "))
            or "freeleech token" in message.lower()
            or "rate limit" in message.lower()
        ):
            grab_error = message
        else:
            grab_error = "Could not complete the grab. Check tracker and qBittorrent settings."

    raw_json["selected"] = {
        "tracker": tracker,
        "format": fmt,
        "encoding": encoding,
        "media": media,
        "remaster": remaster,
        "size_bytes": size_bytes,
    }
    if "selected_manifest" in locals():
        raw_json["selected"]["torrent_manifest"] = selected_manifest.to_dict()

    torrent_option = TorrentOption(
        request_id=album_request.id,
        red_torrent_id=red_torrent_id,
        red_group_id=red_group_id,
        format=fmt,
        encoding=encoding,
        size_bytes=size_bytes,
        seeders=seeders,
        raw_json=json.dumps(raw_json),
    )
    db.add(torrent_option)
    await db.flush()
    album_request.selected_torrent_id = torrent_option.id

    await db.commit()

    return templates.TemplateResponse("partials/grab_response.html", {
        "request": request,
        "album_request": album_request,
        "artist": artist,
        "album": album,
        "tracker_label": tracker_client.label,
        "cross_seed_status": cross_seed_status,
        "cross_seed_target_label": cross_seed_target_label,
        "cross_seed_source_label": tracker_client.label,
        "grab_error": grab_error,
    })
