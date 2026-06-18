from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, time, timedelta
import re

from app.services.lastfm import lastfm_client
from app.services.listenbrainz import lb_client
from app.services.navidrome import get_collection, get_newest_albums, refresh_collection
from app.services.red import get_top_albums as tracker_top_albums
from app.services.redacted import ordered_tracker_names


LASTFM_TOP_TTL = timedelta(hours=1)
RECOMMENDATIONS_TTL = timedelta(hours=2)
TRACKER_TOP_TTL = timedelta(hours=24)
COLLECTION_TTL = timedelta(minutes=10)
LISTENBRAINZ_EMPTY_RETRY_TTL = timedelta(hours=6)
LISTENBRAINZ_RELEASE_TIME = time(hour=12, minute=15)

_LIVE_RELEASE_RE = re.compile(
    r"\blive\b|\bconcert\b|\btour\b|\bbootleg\b|\b\d{4}[-\u2013]\d{2}[-\u2013]\d{2}\b"
    r"|\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)

_lastfm_top_cache: dict = {"data": [], "expires": datetime.min, "updated_at": None}
_recommendations_cache: dict = {"data": [], "expires": datetime.min, "updated_at": None}
_tracker_top_cache: dict = {
    "data": [],
    "expires": datetime.min,
    "updated_at": None,
    "tracker": "",
    "tracker_key": "",
}
_listenbrainz_cache: dict = {
    "data": [],
    "week": "",
    "expires": datetime.min,
    "updated_at": None,
}
_collection_cache: dict = {
    "collection": [],
    "recently_added": [],
    "expires": datetime.min,
    "updated_at": None,
}
_refreshing: set[str] = set()
_background_refresh_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now()


def current_listenbrainz_week(now: datetime | None = None) -> str:
    today = now or _now()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


def _next_listenbrainz_release(now: datetime | None = None) -> datetime:
    current = now or _now()
    monday = current - timedelta(days=current.weekday())
    release = datetime.combine(monday.date(), LISTENBRAINZ_RELEASE_TIME)
    if current >= release:
        release += timedelta(days=7)
    return release


def _tracker_cache_key() -> str:
    return "|".join(ordered_tracker_names())


def clear_home_cache() -> None:
    global _lastfm_top_cache, _recommendations_cache, _tracker_top_cache, _listenbrainz_cache, _collection_cache
    _lastfm_top_cache = {"data": [], "expires": datetime.min, "updated_at": None}
    _recommendations_cache = {"data": [], "expires": datetime.min, "updated_at": None}
    _tracker_top_cache = {
        "data": [],
        "expires": datetime.min,
        "updated_at": None,
        "tracker": "",
        "tracker_key": "",
    }
    _listenbrainz_cache = {
        "data": [],
        "week": "",
        "expires": datetime.min,
        "updated_at": None,
    }
    _collection_cache = {
        "collection": [],
        "recently_added": [],
        "expires": datetime.min,
        "updated_at": None,
    }


def home_cache_is_stale() -> bool:
    now = _now()
    week_key = current_listenbrainz_week(now)
    return (
        now > _lastfm_top_cache["expires"]
        or now > _recommendations_cache["expires"]
        or now > _tracker_top_cache["expires"]
        or _tracker_top_cache.get("tracker_key") != _tracker_cache_key()
        or now > _listenbrainz_cache["expires"]
        or _listenbrainz_cache.get("week") != week_key
        or now > _collection_cache["expires"]
    )


def get_home_cache_snapshot() -> dict:
    return {
        "lastfm_albums": deepcopy(_lastfm_top_cache.get("data") or []),
        "recommendations": deepcopy(_recommendations_cache.get("data") or []),
        "lb_playlists": deepcopy(_listenbrainz_cache.get("data") or []),
        "collection": deepcopy(_collection_cache.get("collection") or []),
        "recently_added": deepcopy(_collection_cache.get("recently_added") or []),
        "red_top": deepcopy(_tracker_top_cache.get("data") or []),
        "tracker_top_label": _tracker_top_cache.get("tracker", ""),
        "tracker_top_site": (
            (_tracker_top_cache.get("data") or [{}])[0].get("tracker_site", "")
            if _tracker_top_cache.get("data")
            else ""
        ),
        "rec_week": current_listenbrainz_week(),
    }


def _begin_refresh(key: str) -> bool:
    if key in _refreshing:
        return False
    _refreshing.add(key)
    return True


def _end_refresh(key: str) -> None:
    _refreshing.discard(key)


async def refresh_lastfm_top_cache(force: bool = False) -> None:
    global _lastfm_top_cache
    now = _now()
    if not force and now <= _lastfm_top_cache["expires"]:
        return
    if not _begin_refresh("lastfm_top"):
        return
    try:
        albums = await lastfm_client.get_top_albums(period="7day", limit=12)
        _lastfm_top_cache = {
            "data": albums,
            "expires": now + LASTFM_TOP_TTL,
            "updated_at": now,
        }
    except Exception:
        pass
    finally:
        _end_refresh("lastfm_top")


async def refresh_recommendations_cache(force: bool = False) -> None:
    global _recommendations_cache
    now = _now()
    if not force and now <= _recommendations_cache["expires"]:
        return
    if not _begin_refresh("recommendations"):
        return
    try:
        recommendations = await lastfm_client.get_weekly_recommendations(limit=18)
        _recommendations_cache = {
            "data": recommendations,
            "expires": now + RECOMMENDATIONS_TTL,
            "updated_at": now,
        }
    except Exception:
        pass
    finally:
        _end_refresh("recommendations")


async def refresh_tracker_top_cache(force: bool = False) -> None:
    global _tracker_top_cache
    now = _now()
    tracker_key = _tracker_cache_key()
    if (
        not force
        and now <= _tracker_top_cache["expires"]
        and _tracker_top_cache.get("tracker_key") == tracker_key
    ):
        return
    if not _begin_refresh("tracker_top"):
        return
    try:
        albums = await tracker_top_albums(period="week", limit=10)
        tracker_label = albums[0].get("tracker_label", "") if albums else ""
        _tracker_top_cache = {
            "data": albums,
            "expires": now + TRACKER_TOP_TTL,
            "updated_at": now,
            "tracker": tracker_label,
            "tracker_key": tracker_key,
        }
    except Exception:
        pass
    finally:
        _end_refresh("tracker_top")


async def _build_listenbrainz_playlists() -> list[dict]:
    playlists = await lb_client.get_weekly_playlists()
    for playlist in playlists:
        enriched = await lastfm_client.enrich_tracks_with_covers(playlist.get("tracks") or [])
        seen_albums = set()
        albums = []
        for track in enriched:
            album_name = track.get("album", "")
            if not album_name or _LIVE_RELEASE_RE.search(album_name):
                continue
            key = f"{track.get('artist', '').lower()}|{album_name.lower()}"
            if key in seen_albums:
                continue
            seen_albums.add(key)
            albums.append({**track, "recommended_track": track.get("title", "")})
        playlist["albums"] = albums
    return playlists


async def refresh_listenbrainz_cache(force: bool = False) -> None:
    global _listenbrainz_cache
    now = _now()
    week_key = current_listenbrainz_week(now)
    if (
        not force
        and now <= _listenbrainz_cache["expires"]
        and _listenbrainz_cache.get("week") == week_key
    ):
        return
    if not _begin_refresh("listenbrainz"):
        return
    try:
        playlists = await _build_listenbrainz_playlists()
        expires = _next_listenbrainz_release(now) if playlists else now + LISTENBRAINZ_EMPTY_RETRY_TTL
        _listenbrainz_cache = {
            "data": playlists,
            "week": week_key,
            "expires": expires,
            "updated_at": now,
        }
    except Exception:
        pass
    finally:
        _end_refresh("listenbrainz")


async def refresh_collection_cache(force: bool = False) -> None:
    global _collection_cache
    now = _now()
    if not force and now <= _collection_cache["expires"]:
        return
    if not _begin_refresh("collection"):
        return
    try:
        collection_task = refresh_collection() if force else get_collection()
        collection, recently_added = await asyncio.gather(
            collection_task,
            get_newest_albums(limit=12),
        )
        _collection_cache = {
            "collection": collection,
            "recently_added": recently_added,
            "expires": now + COLLECTION_TTL,
            "updated_at": now,
        }
    except Exception:
        pass
    finally:
        _end_refresh("collection")


async def refresh_home_cache(force: bool = False) -> None:
    await asyncio.gather(
        refresh_lastfm_top_cache(force=force),
        refresh_recommendations_cache(force=force),
        refresh_tracker_top_cache(force=force),
        refresh_listenbrainz_cache(force=force),
        refresh_collection_cache(force=force),
    )


def _consume_background_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        pass


def schedule_home_cache_refresh_if_stale(force: bool = False) -> None:
    global _background_refresh_task
    if not force and not home_cache_is_stale():
        return
    if _background_refresh_task and not _background_refresh_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _background_refresh_task = loop.create_task(refresh_home_cache(force=force))
    _background_refresh_task.add_done_callback(_consume_background_result)
