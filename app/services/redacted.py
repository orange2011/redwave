import asyncio
import copy
import re
import time
import unicodedata

import httpx
from app.config import settings
from app.utils import normalize_artist


_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\[\]\(\)\{\},.:;!?'\"`/\\|+\-_\u2010-\u2015]")
_TOKEN_ERROR_MESSAGES = (
    "You do not have any freeleech tokens left.",
    "You do not have enough freeleech tokens",
    "This torrent is too large.",
    "You cannot use tokens here",
)
TRACKER_RATE_LIMIT_SECONDS = 60 * 60
TRACKER_REQUEST_SPACING_SECONDS = 1.15
TRACKER_SEARCH_CACHE_SECONDS = 10 * 60
_TRACKER_BACKOFF_UNTIL: dict[str, float] = {}
_TRACKER_RATE_LIMIT_MESSAGES = (
    "temporarily banned",
    "rate limit",
    "rate-limit",
    "too many requests",
)
_VARIOUS_ARTIST_KEYS = {"various", "various artists", "v a", "va"}
FREELEECH_TOKEN_MODES = {"never", "preferred", "required"}
QUALITY_PROFILES = {
    "any": {
        "label": "Any quality",
        "formats": None,
        "encodings": None,
    },
    "flac_any": {
        "label": "FLAC - any lossless",
        "formats": {"FLAC"},
        "encodings": {"lossless", "24bit lossless"},
    },
    "flac_lossless": {
        "label": "FLAC - Lossless",
        "formats": {"FLAC"},
        "encodings": {"lossless"},
    },
    "flac_24bit": {
        "label": "FLAC - 24bit Lossless",
        "formats": {"FLAC"},
        "encodings": {"24bit lossless"},
    },
    "mp3_any": {
        "label": "MP3 - any bitrate",
        "formats": {"MP3"},
        "encodings": None,
    },
    "mp3_v0": {
        "label": "MP3 - V0 (VBR)",
        "formats": {"MP3"},
        "encodings": {"v0", "v0 vbr"},
    },
    "mp3_320": {
        "label": "MP3 - 320",
        "formats": {"MP3"},
        "encodings": {"320"},
    },
    "mp3_v2": {
        "label": "MP3 - V2 (VBR)",
        "formats": {"MP3"},
        "encodings": {"v2", "v2 vbr"},
    },
}
MEDIA_PREFERENCES = {
    "any": "Any media",
    "cd": "CD",
    "web": "WEB",
    "vinyl": "Vinyl",
    "cassette": "Cassette",
    "sacd": "SACD",
    "blu_ray": "Blu-Ray",
    "dvd": "DVD",
    "soundboard": "Soundboard",
}
MEDIA_SCORE_FIELDS = [
    ("CD", "RED_MEDIA_SCORE_CD", "red_media_score_cd", 100),
    ("WEB", "RED_MEDIA_SCORE_WEB", "red_media_score_web", 50),
    ("Vinyl", "RED_MEDIA_SCORE_VINYL", "red_media_score_vinyl", -10000),
    ("Cassette", "RED_MEDIA_SCORE_CASSETTE", "red_media_score_cassette", 0),
    ("SACD", "RED_MEDIA_SCORE_SACD", "red_media_score_sacd", 90),
    ("Blu-Ray", "RED_MEDIA_SCORE_BLU_RAY", "red_media_score_blu_ray", 80),
    ("DVD", "RED_MEDIA_SCORE_DVD", "red_media_score_dvd", 70),
    ("Soundboard", "RED_MEDIA_SCORE_SOUNDBOARD", "red_media_score_soundboard", 20),
]
DEFAULT_MEDIA_ORDER = {
    "CD": 0,
    "WEB": 1,
    "Vinyl": 2,
    "SACD": 3,
    "Cassette": 4,
    "Blu-Ray": 5,
    "DVD": 6,
    "Soundboard": 7,
}


def _clean_query(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _group_key(group: dict) -> str:
    return str(
        group.get("groupId")
        or f"{group.get('artist', '')}|{group.get('groupName', '')}|{group.get('groupYear', '')}"
    )


def _without_punctuation(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = _PUNCT_RE.sub(" ", value)
    return _clean_query(value)


def _track_name(track: dict | str) -> str:
    if isinstance(track, str):
        return track.strip()
    return (track.get("name") or track.get("track") or track.get("title") or "").strip()


def _track_search_titles(tracks: list[dict | str], album: str = "", max_tracks: int = 12) -> list[str]:
    album_key = _without_punctuation(album).casefold()
    candidates: list[tuple[int, int, int, str]] = []
    seen: set[str] = set()
    for index, track in enumerate(tracks or []):
        title = _clean_query(_track_name(track))
        key = _without_punctuation(title).casefold()
        if not key or key in seen or len(key) < 3:
            continue
        seen.add(key)
        album_priority = 0 if album_key and key == album_key else 1
        candidates.append((album_priority, -len(key), index, title))

    candidates.sort()
    return [title for _, _, _, title in candidates[:max_tracks]]


def _same_clean_text(left: str, right: str) -> bool:
    left_key = _without_punctuation(left).casefold()
    right_key = _without_punctuation(right).casefold()
    return bool(left_key and right_key and left_key == right_key)


def _group_artist_matches(group: dict, artist: str) -> bool:
    wanted = normalize_artist(artist)
    found = normalize_artist(group.get("artist", ""))
    return bool(wanted and found and (wanted == found or wanted in found or found in wanted))


def _group_is_various_artist(group: dict) -> bool:
    return normalize_artist(group.get("artist", "")) in _VARIOUS_ARTIST_KEYS


def _album_variants(album: str) -> list[str]:
    variants = [
        album,
        re.sub(r"\band\b", "&", album, flags=re.IGNORECASE),
        album.replace("&", " and "),
        _without_punctuation(album),
        _without_punctuation(re.sub(r"\band\b", "&", album, flags=re.IGNORECASE)),
        _without_punctuation(album.replace("&", " and ")),
    ]

    seen = set()
    unique = []
    for variant in variants:
        variant = _clean_query(variant)
        key = variant.lower()
        if variant and key not in seen:
            unique.append(variant)
            seen.add(key)
    return unique


def normalize_token_mode(value: str | None) -> str:
    mode = (value or "never").strip().lower()
    return mode if mode in FREELEECH_TOKEN_MODES else "never"


def token_mode_label(value: str | None) -> str:
    return {
        "never": "Never",
        "preferred": "Preferred",
        "required": "Required",
    }[normalize_token_mode(value)]


def normalize_quality_profile(value: str | None) -> str:
    profile = (value or "any").strip().lower()
    return profile if profile in QUALITY_PROFILES else "any"


def quality_profile_label(value: str | None) -> str:
    return QUALITY_PROFILES[normalize_quality_profile(value)]["label"]


def quality_profile_options() -> list[tuple[str, str]]:
    return [(key, value["label"]) for key, value in QUALITY_PROFILES.items()]


def normalize_media_preference(value: str | None) -> str:
    media = (value or "any").strip().lower().replace("-", "_")
    return media if media in MEDIA_PREFERENCES else "any"


def media_preference_label(value: str | None) -> str:
    return MEDIA_PREFERENCES[normalize_media_preference(value)]


def media_preference_options() -> list[tuple[str, str]]:
    return list(MEDIA_PREFERENCES.items())


def _coerce_score(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_media_scores() -> dict[str, int]:
    return {label: default for label, _, _, default in MEDIA_SCORE_FIELDS}


def current_media_scores() -> dict[str, int]:
    return {
        label: _coerce_score(getattr(settings, attr, default), default)
        for label, _, attr, default in MEDIA_SCORE_FIELDS
    }


def media_score_options() -> list[dict]:
    return [
        {
            "label": label,
            "env_key": env_key,
            "attr": attr,
            "default": default,
        }
        for label, env_key, attr, default in MEDIA_SCORE_FIELDS
    ]


def _normalize_quality_value(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("(", " ").replace(")", " ")
    return _clean_query(value)


def torrent_matches_quality(torrent: dict, profile: str | None = None) -> bool:
    profile = normalize_quality_profile(profile or settings.red_quality_profile)
    rule = QUALITY_PROFILES[profile]
    formats = rule["formats"]
    encodings = rule["encodings"]
    torrent_format = (torrent.get("format") or "").strip().upper()
    torrent_encoding = _normalize_quality_value(torrent.get("encoding", ""))

    if formats and torrent_format not in formats:
        return False
    if encodings and torrent_encoding not in encodings:
        return False
    return True


def torrent_matches_media(torrent: dict, media_preference: str | None = None) -> bool:
    preference = normalize_media_preference(media_preference or settings.red_media_preference)
    if preference == "any":
        return True
    return (torrent.get("media") or "").strip().lower() == MEDIA_PREFERENCES[preference].lower()


def quality_sort_bucket(torrent: dict, profile: str | None = None) -> int:
    if torrent_matches_quality(torrent, profile):
        return 0
    fmt = (torrent.get("format") or "").upper()
    enc = _normalize_quality_value(torrent.get("encoding", ""))
    if fmt == "FLAC" and enc in {"lossless", "24bit lossless"}:
        return 1
    if fmt == "MP3" and enc in {"v0 vbr", "v0", "320", "v2 vbr", "v2"}:
        return 2
    return 3


def media_sort_bucket(torrent: dict, media_preference: str | None = None) -> int:
    preference = normalize_media_preference(media_preference or settings.red_media_preference)
    media = (torrent.get("media") or "").strip()
    if preference != "any" and media.lower() == MEDIA_PREFERENCES[preference].lower():
        return 0
    return 1 + DEFAULT_MEDIA_ORDER.get(media, 99)


def torrent_media_score(torrent: dict, media_scores: dict[str, int] | None = None) -> int:
    scores = media_scores or current_media_scores()
    media = (torrent.get("media") or "").strip()
    return _coerce_score(scores.get(media), 0)


def quality_preference_score(torrent: dict, profile: str | None = None) -> int:
    profile = normalize_quality_profile(profile or settings.red_quality_profile)
    fmt = (torrent.get("format") or "").upper()
    enc = _normalize_quality_value(torrent.get("encoding", ""))
    if profile != "any" and torrent_matches_quality(torrent, profile):
        return 100
    if fmt == "FLAC" and enc in {"lossless", "24bit lossless"}:
        return 70
    if fmt == "MP3" and enc in {"v0 vbr", "v0", "320", "v2 vbr", "v2"}:
        return 30
    return 0


def torrent_preference_score(torrent: dict, quality_profile: str | None = None, media_scores: dict[str, int] | None = None) -> int:
    return quality_preference_score(torrent, quality_profile) + torrent_media_score(torrent, media_scores)


def media_score_summary(media_scores: dict[str, int] | None = None) -> str:
    scores = media_scores or current_media_scores()
    parts = [f"{label} {scores.get(label, default)}" for label, _, _, default in MEDIA_SCORE_FIELDS]
    return " / ".join(parts)


def torrent_preference_sort_key(torrent: dict, quality_profile: str | None = None, media_scores: dict[str, int] | None = None) -> tuple[int, int, int, int]:
    quality_profile = normalize_quality_profile(quality_profile or settings.red_quality_profile)
    scores = media_scores or current_media_scores()
    return (
        -torrent_preference_score(torrent, quality_profile, scores),
        -torrent_media_score(torrent, scores),
        quality_sort_bucket(torrent, quality_profile),
        DEFAULT_MEDIA_ORDER.get((torrent.get("media") or "").strip(), 99),
    )


def _looks_like_torrent(content: bytes) -> bool:
    return len(content) >= 1 and content[:1] == b"d"


def _has_token_error(content: bytes) -> bool:
    try:
        text = content.decode("utf-8", errors="ignore")
    except Exception:
        return False
    return any(message in text for message in _TOKEN_ERROR_MESSAGES)


def _looks_like_tracker_rate_limit(value: str) -> bool:
    value = (value or "").lower()
    return any(message in value for message in _TRACKER_RATE_LIMIT_MESSAGES)


class TrackerRateLimitError(ValueError):
    pass


class GazelleTrackerClient:
    def __init__(self, tracker: str, label: str, base_url: str, site_url: str, api_key_attr: str):
        self.tracker = tracker
        self.label = label
        self.BASE_URL = base_url
        self.SITE_URL = site_url.rstrip("/")
        self.api_key_attr = api_key_attr
        self._client = httpx.AsyncClient(
            headers={"Authorization": self.api_key},
            timeout=15.0,
            follow_redirects=False,
        )
        self._search_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._track_search_cache: dict[tuple[str, str, tuple[str, ...]], tuple[float, list[dict]]] = {}

    @property
    def api_key(self) -> str:
        return getattr(settings, self.api_key_attr, "")

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    def _sync_auth_header(self):
        self._client.headers["Authorization"] = self.api_key

    def _raise_if_backoff_active(self):
        cooldown_until = _TRACKER_BACKOFF_UNTIL.get(self.tracker, 0)
        if cooldown_until > time.time():
            minutes = max(1, round((cooldown_until - time.time()) / 60))
            raise TrackerRateLimitError(
                f"{self.label} recently reported a temporary ban/rate limit. "
                f"Redwave will not retry for about {minutes} minutes."
            )

    def _record_backoff(self):
        _TRACKER_BACKOFF_UNTIL[self.tracker] = time.time() + TRACKER_RATE_LIMIT_SECONDS

    def _response_preview(self, response: httpx.Response) -> str:
        try:
            text = response.text
        except Exception:
            text = response.content[:512].decode("utf-8", errors="ignore")
        return (text or "").strip()

    def _raise_if_rate_limited(self, response: httpx.Response):
        text = self._response_preview(response)
        if response.status_code == 429 or _looks_like_tracker_rate_limit(text):
            self._record_backoff()
            raise TrackerRateLimitError(
                f"{self.label} reported a temporary ban/rate limit. "
                "Stop testing/searching it for a while; Redwave is backing off automatically."
            )

    def group_url(self, group_id: int | str | None) -> str:
        return f"{self.SITE_URL}/torrents.php?id={group_id}" if group_id else self.SITE_URL

    def _mark_group(self, group: dict, album: str = "") -> dict:
        group["_redwave_search_album"] = album
        group["_redwave_tracker"] = self.tracker
        group["_redwave_tracker_label"] = self.label
        group["_redwave_group_url"] = self.group_url(group.get("groupId"))
        return group

    async def search_torrents(self, artist: str, album: str) -> list[dict]:
        if not self.is_configured():
            return []
        cache_key = (artist.strip().casefold(), album.strip().casefold())
        cached = self._search_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])
        self._sync_auth_header()
        self._raise_if_backoff_active()
        results = []
        seen_group_ids = set()

        for album_variant in _album_variants(album):
            r = await self._client.get(self.BASE_URL, params={
                "action": "browse",
                "searchstr": _clean_query(f"{artist} {album_variant}"),
                "filter_cat[1]": 1,
            })
            if r.status_code in (301, 302, 303, 307, 308):
                raise ValueError(f"{self.label} API key invalid or expired.")
            self._raise_if_rate_limited(r)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                if _looks_like_tracker_rate_limit(data.get("error", "")):
                    self._record_backoff()
                    raise TrackerRateLimitError(
                        f"{self.label} reported a temporary ban/rate limit. "
                        "Stop testing/searching it for a while; Redwave is backing off automatically."
                    )
                continue

            for group in data.get("response", {}).get("results", []):
                key = _group_key(group)
                if key in seen_group_ids:
                    continue
                results.append(self._mark_group(group, album_variant))
                seen_group_ids.add(key)

            if results:
                break

        if not results and artist:
            r = await self._client.get(self.BASE_URL, params={
                "action": "browse",
                "artistname": _clean_query(artist),
                "filter_cat[1]": 1,
            })
            if r.status_code in (301, 302, 303, 307, 308):
                raise ValueError(f"{self.label} API key invalid or expired.")
            self._raise_if_rate_limited(r)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "success":
                for group in data.get("response", {}).get("results", []):
                    key = _group_key(group)
                    if key in seen_group_ids:
                        continue
                    results.append(self._mark_group(group, album))
                    seen_group_ids.add(key)
            elif _looks_like_tracker_rate_limit(data.get("error", "")):
                self._record_backoff()
                raise TrackerRateLimitError(
                    f"{self.label} reported a temporary ban/rate limit. "
                    "Stop testing/searching it for a while; Redwave is backing off automatically."
                )

        if len(self._search_cache) > 256:
            self._search_cache.pop(next(iter(self._search_cache)))
        self._search_cache[cache_key] = (now + TRACKER_SEARCH_CACHE_SECONDS, copy.deepcopy(results))
        return results

    async def search_torrents_by_tracks(
        self,
        artist: str,
        album: str,
        tracks: list[dict | str],
        max_tracks: int = 12,
    ) -> list[dict]:
        if not self.is_configured() or not artist:
            return []

        track_titles = _track_search_titles(tracks, album=album, max_tracks=max_tracks)
        if not track_titles:
            return []
        cache_key = (artist.strip().casefold(), album.strip().casefold(), tuple(title.casefold() for title in track_titles))
        cached = self._track_search_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])

        self._sync_auth_header()
        self._raise_if_backoff_active()
        results_by_key: dict[str, dict] = {}
        hits_by_key: dict[str, list[str]] = {}
        artist_confirmed_keys: set[str] = set()
        minimum_hits = 1 if len(track_titles) == 1 else 2
        last_request_at = 0.0
        query_specs: list[tuple[str, str, bool]] = []
        for track_title in track_titles:
            query_specs.append((track_title, track_title, False))
            if len(track_titles) == 1:
                artist_track_query = _clean_query(f"{artist} {track_title}")
                if artist_track_query and artist_track_query != track_title:
                    query_specs.append((artist_track_query, track_title, True))

        for query, track_title, artist_scoped in query_specs:
            if last_request_at:
                elapsed = time.monotonic() - last_request_at
                if elapsed < TRACKER_REQUEST_SPACING_SECONDS:
                    await asyncio.sleep(TRACKER_REQUEST_SPACING_SECONDS - elapsed)
            last_request_at = time.monotonic()
            r = await self._client.get(self.BASE_URL, params={
                "action": "browse",
                "filelist": query,
                "filter_cat[1]": 1,
            })
            if r.status_code in (301, 302, 303, 307, 308):
                raise ValueError(f"{self.label} API key invalid or expired.")
            self._raise_if_rate_limited(r)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                if _looks_like_tracker_rate_limit(data.get("error", "")):
                    self._record_backoff()
                    raise TrackerRateLimitError(
                        f"{self.label} reported a temporary ban/rate limit. "
                        "Stop testing/searching it for a while; Redwave is backing off automatically."
                    )
                continue

            for group in data.get("response", {}).get("results", []):
                key = _group_key(group)
                results_by_key.setdefault(key, group)
                hits = hits_by_key.setdefault(key, [])
                if track_title not in hits:
                    hits.append(track_title)
                if artist_scoped:
                    artist_confirmed_keys.add(key)

        matches = []
        for key, group in results_by_key.items():
            hits = hits_by_key.get(key, [])
            title_track_hit = any(_same_clean_text(hit, album) for hit in hits)
            artist_match = _group_artist_matches(group, artist)
            artist_confirmed = key in artist_confirmed_keys
            various_artist_match = _group_is_various_artist(group) and artist_confirmed
            if len(track_titles) == 1 and not (artist_match or various_artist_match):
                continue
            if len(hits) < minimum_hits and not (title_track_hit and artist_match):
                continue
            marked = self._mark_group(group, album)
            marked["_redwave_search_mode"] = "track_fallback"
            marked["_redwave_track_hits"] = hits
            marked["_redwave_track_hit_count"] = len(hits)
            marked["_redwave_track_artist_confirmed"] = artist_confirmed
            matches.append(marked)

        matches.sort(key=lambda group: (
            -int(group.get("_redwave_track_hit_count") or 0),
            str(group.get("artist") or "").casefold(),
            str(group.get("groupName") or "").casefold(),
        ))
        if len(self._track_search_cache) > 128:
            self._track_search_cache.pop(next(iter(self._track_search_cache)))
        self._track_search_cache[cache_key] = (now + TRACKER_SEARCH_CACHE_SECONDS, copy.deepcopy(matches))
        return matches

    async def get_torrent_file(self, torrent_id: int, use_token: bool = False, token_mode: str | None = None) -> bytes:
        if not self.is_configured():
            raise ValueError(f"{self.label} API key is not configured.")
        self._sync_auth_header()
        self._raise_if_backoff_active()
        token_mode = normalize_token_mode(token_mode or settings.red_use_freeleech_token)
        params = {
            "action": "download",
            "id": torrent_id,
        }
        if self.tracker == "red":
            params["usetoken"] = 1 if use_token else 0
        r = await self._client.get(self.BASE_URL, params=params)
        self._raise_if_rate_limited(r)
        r.raise_for_status()
        content = r.content

        if use_token and token_mode == "preferred" and not _looks_like_torrent(content) and _has_token_error(content):
            r = await self._client.get(self.BASE_URL, params={
                "action": "download",
                "id": torrent_id,
                "usetoken": 0,
            })
            self._raise_if_rate_limited(r)
            r.raise_for_status()
            return r.content

        if use_token and token_mode == "required" and not _looks_like_torrent(content) and _has_token_error(content):
            raise ValueError("RED could not apply a freeleech token to this torrent.")

        return content

    async def get_torrent_info(self, torrent_id: int) -> dict:
        if not self.is_configured():
            return {}
        self._sync_auth_header()
        self._raise_if_backoff_active()
        r = await self._client.get(self.BASE_URL, params={
            "action": "torrent",
            "id": torrent_id,
        })
        self._raise_if_rate_limited(r)
        r.raise_for_status()
        data = r.json()
        return data.get("response", {})

    async def get_torrent_group(self, group_id: int | str) -> dict:
        if not self.is_configured():
            return {}
        self._sync_auth_header()
        self._raise_if_backoff_active()
        r = await self._client.get(self.BASE_URL, params={
            "action": "torrentgroup",
            "id": group_id,
        })
        self._raise_if_rate_limited(r)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            if _looks_like_tracker_rate_limit(data.get("error", "")):
                self._record_backoff()
                raise TrackerRateLimitError(
                    f"{self.label} reported a temporary ban/rate limit. "
                    "Stop testing/searching it for a while; Redwave is backing off automatically."
                )
            raise ValueError(data.get("error", "RED torrent group lookup failed"))
        return data.get("response", {})

    async def get_artist_info(self, artist_id: int | str | None = None, artist_name: str = "") -> dict:
        if not self.is_configured():
            return {}
        self._sync_auth_header()
        params = {"action": "artist"}
        if artist_id:
            params["id"] = artist_id
        elif artist_name:
            params["artistname"] = artist_name
        else:
            return {}
        self._raise_if_backoff_active()
        r = await self._client.get(self.BASE_URL, params=params)
        self._raise_if_rate_limited(r)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            if _looks_like_tracker_rate_limit(data.get("error", "")):
                self._record_backoff()
                raise TrackerRateLimitError(
                    f"{self.label} reported a temporary ban/rate limit. "
                    "Stop testing/searching it for a while; Redwave is backing off automatically."
                )
            raise ValueError(data.get("error", "RED artist lookup failed"))
        return data.get("response", {})


class RedactedClient(GazelleTrackerClient):
    def __init__(self):
        super().__init__(
            tracker="red",
            label="RED",
            base_url="https://redacted.sh/ajax.php",
            site_url="https://redacted.sh",
            api_key_attr="red_api_key",
        )


class OrpheusClient(GazelleTrackerClient):
    def __init__(self):
        super().__init__(
            tracker="ops",
            label="OPS",
            base_url="https://orpheus.network/ajax.php",
            site_url="https://orpheus.network",
            api_key_attr="ops_api_key",
        )


red_client = RedactedClient()
ops_client = OrpheusClient()
TRACKER_CLIENTS = {
    "red": red_client,
    "ops": ops_client,
}


def tracker_client_for(value: str | None) -> GazelleTrackerClient:
    return TRACKER_CLIENTS.get((value or "red").strip().lower(), red_client)
