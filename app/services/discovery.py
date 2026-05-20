import re
from collections import defaultdict

from app.utils import collection_key, fuzzy_match_score, fuzzy_matches, normalize_artist


GENRE_PRESETS = [
    "metal",
    "black metal",
    "death metal",
    "rock",
    "indie",
    "indie pop",
    "electronic",
    "ambient",
    "hip-hop",
    "rap",
    "pop",
    "country",
    "folk",
    "jazz",
    "soul",
    "rnb",
    "punk",
    "post-punk",
    "shoegaze",
    "experimental",
]

GENRE_ALIASES = {
    "hip hop": "hip-hop",
    "hiphop": "hip-hop",
    "r&b": "rnb",
    "rhythm and blues": "rnb",
    "post punk": "post-punk",
    "indie-pop": "indie pop",
    "blackmetal": "black metal",
    "deathmetal": "death metal",
}

_GAP_JUNK_RE = re.compile(
    r"\b(demo|live|remix|radio edit|karaoke|instrumental|tribute|cover version|sped up|slowed|nightcore)\b",
    re.IGNORECASE,
)


def genre_slug(genre: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", genre.lower()).strip("-")


def coerce_genre(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if not value:
        return GENRE_PRESETS[0]
    value = GENRE_ALIASES.get(value, value)
    presets_by_slug = {genre_slug(genre): genre for genre in GENRE_PRESETS}
    return presets_by_slug.get(genre_slug(value), value)


def collection_keys(collection: list[dict]) -> set[str]:
    return {
        collection_key(album.get("artist", ""), album.get("album", ""))
        for album in collection
        if album.get("artist") and album.get("album")
    }


def mark_collection(albums: list[dict], owned_keys: set[str]) -> list[dict]:
    marked = []
    for album in albums:
        marked.append({
            **album,
            "in_collection": collection_key(album.get("artist", ""), album.get("album", "")) in owned_keys,
        })
    return marked


def artist_gap_targets(
    collection: list[dict],
    artist_filter: str = "",
    max_artists: int = 12,
) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    display_names: dict[str, str] = {}

    for album in collection:
        artist = (album.get("artist") or "").strip()
        if not artist:
            continue
        key = normalize_artist(artist)
        grouped[key].append(album)
        display_names.setdefault(key, artist)

    query = artist_filter.strip()
    targets = []
    for key, albums in grouped.items():
        artist = display_names[key]
        match_score = fuzzy_match_score(query, artist) if query else 0
        if query and not fuzzy_matches(query, artist):
            continue
        latest_added = max((a.get("added_at") or 0 for a in albums), default=0)
        targets.append({
            "artist": artist,
            "album_count": len(albums),
            "latest_added": latest_added,
            "match_score": match_score,
        })

    if query:
        targets.sort(key=lambda item: (-item["match_score"], -item["album_count"], item["artist"].lower()))
    else:
        targets.sort(key=lambda item: (-item["latest_added"], -item["album_count"], item["artist"].lower()))
    return targets[:max_artists]


def missing_albums_for_artist(
    artist: str,
    candidate_albums: list[dict],
    owned_keys: set[str],
    limit: int = 6,
) -> list[dict]:
    seen: set[str] = set()
    missing = []
    for album in candidate_albums:
        title = album.get("album", "")
        album_artist = album.get("artist") or artist
        if not title:
            continue
        if _GAP_JUNK_RE.search(title):
            continue
        key = collection_key(album_artist, title)
        dedupe_key = collection_key(artist, title)
        if key in owned_keys or dedupe_key in owned_keys or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        missing.append({
            **album,
            "artist": album_artist,
            "in_collection": False,
        })
        if len(missing) >= limit:
            break
    return missing
