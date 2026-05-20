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
    media_score_summary,
    normalize_quality_profile,
    normalize_token_mode,
    ops_client,
    quality_profile_label,
    red_client,
    token_mode_label,
    tracker_client_for,
    torrent_media_score,
    torrent_preference_sort_key,
    torrent_preference_score,
)
from app.models.request import AlbumRequest, TorrentOption
from app.database import get_db
from app.utils import normalize_album
from app.config import settings

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


def _match_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = value.replace("&", " and ")
    value = normalize_album(value)
    return " ".join(_TOKEN_RE.findall(value))


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
    token_mode: str,
    quality_profile: str,
    media_scores: dict[str, int],
) -> list[dict]:
    torrent_list = []
    for group in groups:
        group_id = group.get("groupId")
        tracker = group.get("_redwave_tracker", "red")
        tracker_label = group.get("_redwave_tracker_label", "RED" if tracker == "red" else "OPS")
        group_url = group.get("_redwave_group_url") or tracker_client_for(tracker).group_url(group_id)
        g_artist = group.get("artist", artist)
        g_album = group.get("groupName", album)
        g_year = group.get("groupYear", year)
        album_score = _album_match_score(album, g_album)
        match_score = _group_match_score(group, artist, album, year)
        year_mismatch = bool(year and g_year and str(g_year) != str(year))
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
            can_use_token = tracker == "red" and bool(t.get("canUseToken"))
            will_use_token = tracker == "red" and token_mode in ("preferred", "required") and can_use_token and not free
            if tracker == "red" and token_mode == "required" and not can_use_token and not free:
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
                "size_bytes": size,
                "size_human": _fmt_size(size),
                "seeders": seeders,
                "leechers": leechers,
                "age_days": age,
                "freeleech": free,
                "can_use_token": can_use_token,
                "will_use_token": will_use_token,
                "token_mode": token_mode,
                "quality_profile": quality_profile,
                "media_score": torrent_media_score(t, media_scores),
                "preference_score": torrent_preference_score(t, quality_profile, media_scores),
                "has_log": has_log,
                "log_score": log_score,
                "has_cue": has_cue,
                "uploader": t.get("username", ""),
                "match_score": match_score,
            })

    return _sort_torrent_rows(torrent_list, quality_profile, media_scores)


def _sort_torrent_rows(
    torrent_list: list[dict],
    quality_profile: str,
    media_scores: dict[str, int],
) -> list[dict]:
    torrent_list.sort(key=lambda x: (
        -x["match_score"],
        0 if x.get("tracker") == "red" else 1,
        torrent_preference_sort_key(x, quality_profile, media_scores),
        -x["seeders"],
    ))
    return torrent_list


def _source_note(red_count: int, ops_count: int, ops_configured: bool) -> str:
    if red_count and ops_count:
        return f"Showing RED and OPS results ({red_count} RED, {ops_count} OPS)."
    if red_count:
        return "Showing RED results." if ops_configured else "Showing RED results. Add an OPS API key in Settings to compare both trackers."
    if ops_count:
        return "RED had no matching release. Showing OPS results."
    return ""


async def _find_ops_cross_seed_match(
    artist: str,
    album: str,
    year: str,
    size_bytes: int,
    token_mode: str,
    quality_profile: str,
    media_scores: dict[str, int],
) -> dict | None:
    if not _truthy(settings.ops_cross_seed) or not ops_client.is_configured() or size_bytes <= 0:
        return None

    try:
        ops_results = await ops_client.search_torrents(artist, normalize_album(album))
    except Exception:
        return None

    rows = _build_torrent_rows(
        ops_results,
        artist,
        normalize_album(album),
        year,
        token_mode,
        quality_profile,
        media_scores,
    )
    for row in rows:
        if int(row.get("size_bytes") or 0) == int(size_bytes):
            return row
    return None


@router.get("/torrents/search", response_class=HTMLResponse)
async def search_torrents(
    request: Request,
    mb_id: str = Query(...),
    artist: str = Query(...),
    album: str = Query(...),
    year: str = Query(default=""),
    cover_url: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    search_album = normalize_album(album)
    token_mode = normalize_token_mode(settings.red_use_freeleech_token)
    quality_profile = normalize_quality_profile(settings.red_quality_profile)
    media_scores = current_media_scores()
    red_error = ""
    ops_error = ""

    async def _search_red() -> list[dict]:
        nonlocal red_error
        try:
            return await red_client.search_torrents(artist, search_album)
        except ValueError as e:
            red_error = str(e)
            return []
        except Exception:
            return []

    async def _search_ops() -> list[dict]:
        nonlocal ops_error
        if not ops_client.is_configured():
            return []
        try:
            return await ops_client.search_torrents(artist, search_album)
        except ValueError as e:
            ops_error = str(e)
            return []
        except Exception:
            return []

    red_results, ops_results = await asyncio.gather(_search_red(), _search_ops())

    red_results = sorted(
        red_results,
        key=lambda group: _group_match_score(group, artist, search_album, year),
        reverse=True,
    )
    ops_results = sorted(
        ops_results,
        key=lambda group: _group_match_score(group, artist, search_album, year),
        reverse=True,
    )

    red_rows = _build_torrent_rows(
        red_results, artist, search_album, year, token_mode, quality_profile, media_scores
    )
    ops_rows = _build_torrent_rows(
        ops_results, artist, search_album, year, token_mode, quality_profile, media_scores
    )
    torrent_list = _sort_torrent_rows(red_rows + ops_rows, quality_profile, media_scores)
    source_note = _source_note(len(red_rows), len(ops_rows), ops_client.is_configured())

    error = " / ".join(e for e in (red_error, ops_error) if e) if not torrent_list else ""

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
        "freeleech_token_mode": token_mode,
        "freeleech_token_label": token_mode_label(token_mode),
        "quality_profile": quality_profile,
        "quality_profile_label": quality_profile_label(quality_profile),
        "media_score_summary": media_score_summary(media_scores),
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
    tracker = form.get("tracker", "red")
    tracker_client = tracker_client_for(tracker)
    token_mode = normalize_token_mode(settings.red_use_freeleech_token)
    quality_profile = normalize_quality_profile(settings.red_quality_profile)
    media_scores = current_media_scores()
    cross_seed_status = ""

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
        torrent_bytes = await tracker_client.get_torrent_file(
            red_torrent_id,
            use_token=use_freeleech_token if tracker == "red" else False,
            token_mode=freeleech_token_mode,
        )
        qbt_tag = settings.qbt_red_tag if tracker == "red" else settings.qbt_ops_tag
        success = await qbt_client.add_torrent(torrent_bytes, tags=[qbt_tag])
        if success:
            album_request.status = "downloading"
            if tracker == "red" and _truthy(settings.ops_cross_seed):
                ops_match = await _find_ops_cross_seed_match(
                    artist,
                    album,
                    year,
                    size_bytes,
                    token_mode,
                    quality_profile,
                    media_scores,
                )
                if ops_match:
                    raw_json["cross_seed"] = {
                        "ops": {
                            "status": "pending",
                            "torrent_id": ops_match.get("torrent_id"),
                            "group_id": ops_match.get("group_id"),
                            "title": ops_match.get("title", ""),
                            "size_bytes": ops_match.get("size_bytes", 0),
                        }
                    }
                    cross_seed_status = "queued"
                else:
                    raw_json["cross_seed"] = {"ops": {"status": "no_match"}}
                    cross_seed_status = "no_match"
        else:
            album_request.status = "failed"
    except Exception as e:
        album_request.status = "failed"

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
        "cross_seed_status": cross_seed_status,
    })
