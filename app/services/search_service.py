import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import html
import re
import unicodedata

import httpx

from app.services.lastfm import get_artist_image, lastfm_client
from app.services.navidrome import get_collection, search_library
from app.services.url_import import is_url, resolve_url

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ARTIST_SEPARATOR_RE = re.compile(r"\s*[•·;]\s*")
_TRIM_RE = re.compile(r"^[\s\-_–—.,:;!?()\[\]{}\"']+|[\s\-_–—.,:;!?()\[\]{}\"']+$")
_CONNECTOR_TOKENS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
_TEXT_SEARCH_CACHE: dict[tuple[str, bool], dict] = {}
_TEXT_SEARCH_TTL = timedelta(minutes=5)
_TEXT_SEARCH_CACHE_LIMIT = 64


def match_text(value: str) -> str:
    value = (value or "").lower().replace("&", " and ")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(_TOKEN_RE.findall(value))


def compact_text(value: str) -> str:
    tokens = [token for token in match_text(value).split() if token not in _CONNECTOR_TOKENS]
    return " ".join(tokens)


def _token_set(value: str) -> set[str]:
    return set(compact_text(value).split())


def equivalent_text(a: str, b: str) -> bool:
    a_norm = match_text(a)
    b_norm = match_text(b)
    if not a_norm or not b_norm:
        return False
    return a_norm == b_norm or compact_text(a) == compact_text(b)


def text_score(query: str, value: str, exact: int, contains: int, overlap: int) -> int:
    q_norm = match_text(query)
    value_norm = match_text(value)
    if not q_norm or not value_norm:
        return 0
    if q_norm == value_norm:
        return exact

    q_compact = compact_text(query)
    value_compact = compact_text(value)
    if q_compact and q_compact == value_compact:
        return exact - 2

    if min(len(q_norm), len(value_norm)) >= 3 and (q_norm in value_norm or value_norm in q_norm):
        return contains
    if (
        q_compact
        and value_compact
        and min(len(q_compact), len(value_compact)) >= 3
        and (q_compact in value_compact or value_compact in q_compact)
    ):
        return contains - 3

    q_tokens = _token_set(query)
    value_tokens = _token_set(value)
    if not q_tokens or not value_tokens:
        return 0
    return int(overlap * (len(q_tokens & value_tokens) / len(q_tokens)))


def _album_key(album: dict) -> str:
    return f"{match_text(album.get('artist', ''))}|{match_text(album.get('album', ''))}"


def _track_key(track: dict) -> str:
    return "|".join(
        [
            match_text(track.get("artist", "")),
            match_text(track.get("track", "")),
            match_text(track.get("album", "")),
        ]
    )


def _artist_key(artist: dict) -> str:
    return match_text(artist.get("name", ""))


def _merge_unique(items: list[dict], key_fn) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = key_fn(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def library_query_variants(query: str) -> list[str]:
    """Build forgiving Navidrome searches for messy multi-artist credits."""
    query = (query or "").strip()
    if not query:
        return []

    variants: list[str] = [query]
    parts = [part.strip() for part in _ARTIST_SEPARATOR_RE.split(query) if part.strip()]
    if len(parts) > 1:
        variants.extend(parts[-3:])

    raw_tokens = [
        _TRIM_RE.sub("", token)
        for token in query.split()
    ]
    raw_tokens = [token for token in raw_tokens if len(token) >= 2]
    for size in (1, 2, 3):
        if len(raw_tokens) >= size:
            variants.append(" ".join(raw_tokens[-size:]))

    seen = set()
    out = []
    for variant in variants:
        key = match_text(variant)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(variant)
    return out[:6]


async def search_library_variants(
    query: str,
    artist_count: int = 5,
    album_count: int = 14,
    song_count: int = 12,
) -> dict:
    variants = library_query_variants(query)
    if not variants:
        return {"artists": [], "albums": [], "tracks": []}

    results = await asyncio.gather(
        *(search_library(term, artist_count=artist_count, album_count=album_count, song_count=song_count) for term in variants),
        return_exceptions=True,
    )
    albums: list[dict] = []
    tracks: list[dict] = []
    artists: list[dict] = []
    for result in results:
        if isinstance(result, Exception):
            continue
        albums.extend(result.get("albums", []))
        tracks.extend(result.get("tracks", []))
        artists.extend(result.get("artists", []))

    return {
        "artists": _merge_unique(artists, _artist_key),
        "albums": _merge_unique(albums, _album_key),
        "tracks": _merge_unique(tracks, _track_key),
    }


def _source_bonus(item: dict) -> int:
    score = 0
    if item.get("in_collection"):
        score += 18
    if item.get("source") == "navidrome":
        score += 10
    return score


def album_score(query: str, album: dict) -> int:
    artist = album.get("artist", "")
    title = album.get("album", "")
    score = max(
        text_score(query, title, exact=178, contains=112, overlap=58),
        text_score(query, f"{artist} {title}", exact=190, contains=128, overlap=64),
    )
    if equivalent_text(query, title):
        score += 28
    if compact_text(artist) and compact_text(title) and compact_text(query) == compact_text(f"{artist} {title}"):
        score += 34
    if equivalent_text(query, title) and equivalent_text(query, artist):
        score -= 46
    if album.get("cover_url"):
        score += 6
    if album.get("song_count"):
        score += 2
    return score + _source_bonus(album)


def track_score(query: str, track: dict) -> int:
    artist = track.get("artist", "")
    title = track.get("track", "")
    album = track.get("album", "")
    score = max(
        text_score(query, title, exact=170, contains=118, overlap=58),
        text_score(query, f"{artist} {title}", exact=188, contains=132, overlap=64),
        text_score(query, f"{artist} {album} {title}", exact=180, contains=124, overlap=62),
    )
    if equivalent_text(query, title):
        score += 24
    if compact_text(artist) and compact_text(query) == compact_text(f"{artist} {title}"):
        score += 36
    if track.get("cover_url"):
        score += 4
    return score + _source_bonus(track)


def artist_score(query: str, artist: dict) -> int:
    name = artist.get("name", "")
    score = text_score(query, name, exact=156, contains=98, overlap=48)
    listeners = artist.get("listeners") or 0
    try:
        listeners = int(listeners)
    except (TypeError, ValueError):
        listeners = 0
    if equivalent_text(query, name):
        if listeners >= 10000:
            score += 34
        elif listeners >= 1000:
            score += 22
        elif listeners >= 100:
            score += 10
        else:
            score += 6
    elif listeners >= 100000:
        score += 6
    elif listeners >= 10000:
        score += 4
    if artist.get("image"):
        score += 3
    return score + _source_bonus(artist)


def search_collection(query: str, collection: list[dict], limit: int = 12) -> list[dict]:
    matches = []
    for album in collection:
        item = {**album, "in_collection": True}
        score = album_score(query, item)
        if score > 42:
            matches.append((score, item))
    return [item for _, item in sorted(matches, key=lambda row: row[0], reverse=True)[:limit]]


async def fill_artist_images(artists: list[dict], albums: list[dict], tracks: list[dict]) -> list[dict]:
    missing = [a for a in artists if a.get("name") and not a.get("image")]
    if not missing:
        return artists

    images = await asyncio.gather(
        *(get_artist_image(a["name"]) for a in missing),
        return_exceptions=True,
    )
    for artist, image in zip(missing, images):
        if isinstance(image, str) and image:
            artist["image"] = image

    for artist in missing:
        if artist.get("image"):
            continue
        name = match_text(artist.get("name", ""))
        fallback = next(
            (
                item.get("cover_url")
                for item in albums + tracks
                if item.get("cover_url") and name and (
                    name == match_text(item.get("artist", ""))
                    or name in match_text(item.get("artist", ""))
                    or match_text(item.get("artist", "")) in name
                )
            ),
            None,
        )
        if fallback:
            artist["image"] = fallback
    return artists


def _pick_top_result(
    query: str,
    artists: list[dict],
    collection_hits: list[dict],
    albums: list[dict],
    tracks: list[dict],
) -> dict | None:
    q_norm = match_text(query)
    if not q_norm:
        return None

    candidates: list[tuple[int, int, dict]] = []
    for album in collection_hits + albums:
        score = album_score(query, album)
        candidates.append((score, 2 if album.get("in_collection") else 0, {**album, "type": "album"}))
    for track in tracks:
        score = track_score(query, track)
        candidates.append((score, 3 if track.get("in_collection") else 1, {**track, "type": "track"}))
    for artist in artists:
        score = artist_score(query, artist)
        candidates.append((score, 1 if artist.get("in_collection") else 0, {**artist, "type": "artist"}))

    if not candidates:
        return None
    score, _, top = max(candidates, key=lambda item: (item[0], item[1]))
    return top if score > 0 else None


def _artist_results_for_query(query: str, top_result: dict | None, artists: list[dict]) -> list[dict]:
    if top_result and top_result.get("type") == "artist" and equivalent_text(top_result.get("name", ""), query):
        return [top_result]
    return artists


def _clean_summary(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _short_summary(value: str, limit: int = 320) -> str:
    text = _clean_summary(value)
    if len(text) <= limit:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    picked = ""
    for sentence in sentences:
        next_text = f"{picked} {sentence}".strip()
        if len(next_text) > limit:
            break
        picked = next_text
    if picked:
        return picked

    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def _format_count(value: int | str | None) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return ""


def _unique_facts(*groups: list[str]) -> list[str]:
    seen = set()
    facts = []
    for group in groups:
        for item in group:
            value = str(item or "").strip()
            key = value.lower()
            if value and key not in seen:
                seen.add(key)
                facts.append(value)
    return facts[:5]


async def enrich_top_result(top_result: dict | None) -> dict | None:
    if not top_result:
        return top_result

    top = dict(top_result)
    result_type = top.get("type")

    if result_type == "artist":
        info = await lastfm_client.get_artist_info(top.get("name", ""))
        if info:
            top["image"] = top.get("image") or info.get("image")
            top["listeners"] = top.get("listeners") or info.get("listeners")
            facts = _unique_facts(
                info.get("tags", [])[:3],
                [f"{_format_count(info.get('playcount'))} scrobbles" if info.get("playcount") else ""],
            )
            text = _short_summary(info.get("bio", ""), limit=720)
            if text:
                top["about"] = {
                    "heading": f"About {info.get('name') or top.get('name')}",
                    "text": text,
                    "facts": facts,
                }
        elif top.get("red_summary"):
            top["about"] = {
                "heading": f"About {top.get('name')}",
                "text": _short_summary(top.get("red_summary", ""), limit=720),
                "facts": (top.get("red_tags") or [])[:4],
            }

    elif result_type == "album":
        artist = top.get("artist", "")
        album = top.get("album", "")
        album_info, itunes_info = await asyncio.gather(
            lastfm_client.get_album_info(artist, album),
            lastfm_client.get_itunes_info(artist, album),
        )
        album_info = album_info or {}
        itunes_info = itunes_info or {}
        top["cover_url"] = top.get("cover_url") or album_info.get("cover_url")
        top["mb_id"] = top.get("mb_id") or album_info.get("mb_id")

        year = top.get("year") or itunes_info.get("year", "")
        genre = itunes_info.get("genre", "")
        track_count = top.get("song_count") or itunes_info.get("track_count") or len(album_info.get("tracks", []))
        label = itunes_info.get("label", "")
        release_type = itunes_info.get("release_type") or "Album"
        facts = _unique_facts(
            [year, genre, f"{track_count} tracks" if track_count else "", label],
            top.get("red_tags", [])[:3],
            album_info.get("tags", [])[:3],
        )
        text = _short_summary(top.get("red_summary", ""), limit=520) or _short_summary(album_info.get("summary", ""), limit=360)
        if not text:
            release_label = release_type.lower()
            article = "an" if release_label[:1] in "aeiou" else "a"
            parts = [f"{album} is {article} {release_label} by {artist}"]
            if year:
                parts.append(f"released in {year}")
            if genre:
                parts.append(f"filed under {genre}")
            if track_count:
                parts.append(f"with {track_count} tracks")
            text = ", ".join(parts) + "."
        top["about"] = {
            "heading": f"About {album}",
            "text": text,
            "facts": facts,
        }

    elif result_type == "track":
        artist = top.get("artist", "")
        track = top.get("track", "")
        info = await lastfm_client.get_track_info(artist, track)
        info = info or {}
        album = info.get("album") or top.get("album", "")
        facts = _unique_facts(
            [
                top.get("duration") or info.get("duration", ""),
                f"{_format_count(info.get('listeners'))} listeners" if info.get("listeners") else "",
            ],
            info.get("tags", [])[:3],
        )
        text = _short_summary(info.get("summary", ""), limit=360)
        if not text:
            text = f"{track} is a song by {info.get('artist') or artist}"
            if album:
                text += f" from {album}"
            text += "."
        top["about"] = {
            "heading": f"About {track}",
            "text": text,
            "facts": facts,
        }

    return top


async def _search_musicbrainz(query: str, limit: int = 10) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://musicbrainz.org/ws/2/release-group",
                params={"query": query, "fmt": "json", "limit": limit},
                headers={"User-Agent": "Redwave/1.0 (redwave@example.invalid)"},
            )
            rgs = r.json().get("release-groups", [])
            results = []
            for rg in rgs:
                credits = rg.get("artist-credit") or []
                artist = credits[0].get("artist", {}).get("name", "") if credits else ""
                album = rg.get("title", "")
                mb_id = rg.get("id", "")
                if artist and album:
                    results.append(
                        {
                            "artist": artist,
                            "album": album,
                            "mb_id": mb_id,
                            "cover_url": None,
                            "source": "mb",
                        }
                    )
            return results
    except Exception:
        return []


async def _run_text_search_uncached(query: str, prefer_artist_albums: bool = False):
    collection, library, lfm_albums, lfm_artists, lfm_tracks = await asyncio.gather(
        get_collection(),
        search_library_variants(query, artist_count=5, album_count=14, song_count=12),
        (
            lastfm_client.get_artist_top_albums(query, limit=20)
            if prefer_artist_albums
            else lastfm_client.search_albums(query, limit=20)
        ),
        lastfm_client.search_artists(query, limit=5),
        lastfm_client.search_tracks(query, limit=10),
    )

    nav_albums = library.get("albums", [])
    nav_tracks = library.get("tracks", [])
    nav_artists = library.get("artists", [])
    collection_hits = _merge_unique(
        sorted(
            nav_albums + search_collection(query, collection),
            key=lambda album: album_score(query, album),
            reverse=True,
        ),
        _album_key,
    )[:12]

    library_album_keys = {_album_key(album) for album in collection_hits}
    albums = _merge_unique(
        sorted(
            [album for album in nav_albums + lfm_albums if _album_key(album) not in library_album_keys],
            key=lambda album: album_score(query, album),
            reverse=True,
        ),
        _album_key,
    )

    if len(albums) < 5:
        mb_results = await _search_musicbrainz(query, limit=10)
        existing = library_album_keys | {_album_key(album) for album in albums}
        extras = [album for album in mb_results if _album_key(album) not in existing]
        albums = _merge_unique(
            sorted(albums + extras, key=lambda album: album_score(query, album), reverse=True),
            _album_key,
        )

    tracks = _merge_unique(
        sorted(nav_tracks + lfm_tracks, key=lambda track: track_score(query, track), reverse=True),
        _track_key,
    )[:12]

    artists = _merge_unique(
        sorted(nav_artists + lfm_artists, key=lambda artist: artist_score(query, artist), reverse=True),
        _artist_key,
    )[:8]
    artists = await fill_artist_images(artists, collection_hits + albums, tracks)
    return albums, artists, tracks, collection_hits


async def _run_text_search(query: str, prefer_artist_albums: bool = False):
    cache_key = (match_text(query) or query.strip().casefold(), prefer_artist_albums)
    now = datetime.now()
    cached = _TEXT_SEARCH_CACHE.get(cache_key)
    if cached and now < cached["expires"]:
        return deepcopy(cached["data"])

    data = await _run_text_search_uncached(query, prefer_artist_albums)
    if len(_TEXT_SEARCH_CACHE) > _TEXT_SEARCH_CACHE_LIMIT:
        _TEXT_SEARCH_CACHE.pop(next(iter(_TEXT_SEARCH_CACHE)))
    _TEXT_SEARCH_CACHE[cache_key] = {"data": deepcopy(data), "expires": now + _TEXT_SEARCH_TTL}
    return data


def _merge_resolved_artist(artists: list[dict], info: dict) -> list[dict]:
    name = info.get("artist", "")
    if not name:
        return artists
    name_norm = match_text(name)
    for artist in artists:
        if match_text(artist.get("name", "")) == name_norm:
            artist["image"] = artist.get("image") or info.get("image")
            artist["mb_id"] = artist.get("mb_id") or info.get("mb_id", "")
            artist["source"] = artist.get("source") or info.get("source", "")
            artist["red_summary"] = artist.get("red_summary") or info.get("red_summary", "")
            artist["red_tags"] = artist.get("red_tags") or info.get("red_tags", [])
            return artists
    return [
        {
            "name": name,
            "image": info.get("image"),
            "mb_id": info.get("mb_id", ""),
            "source": info.get("source", ""),
            "red_summary": info.get("red_summary", ""),
            "red_tags": info.get("red_tags", []),
            "listeners": 0,
        },
        *artists,
    ]


async def _run_url_search(query: str):
    info = await resolve_url(query)
    if not info:
        return [], [], [], [], query

    if info.get("kind") == "artist" and info.get("artist"):
        search_term = info["artist"]
        albums, artists, tracks, collection_hits = await _run_text_search(search_term, prefer_artist_albums=True)
        artists = _merge_resolved_artist(artists, info)
        return albums, artists, tracks, collection_hits, search_term

    if info.get("artist") and info.get("album"):
        search_term = f"{info['artist']} {info['album']}"
        albums, artists, tracks, collection_hits = await _run_text_search(search_term)
        artist_key = match_text(info["artist"])
        album_key = match_text(info["album"])
        exact = next(
            (
                album
                for album in collection_hits + albums
                if match_text(album.get("artist", "")) == artist_key and match_text(album.get("album", "")) == album_key
            ),
            None,
        )
        resolved = {
            "mb_id": info.get("mb_id", ""),
            "artist": info["artist"],
            "album": info["album"],
            "cover_url": info.get("cover_url"),
            "deezer_id": info.get("deezer_id", ""),
            "year": info.get("year", ""),
            "red_group_id": info.get("red_group_id", ""),
            "red_summary": info.get("red_summary", ""),
            "red_tags": info.get("red_tags", []),
            "source": info.get("source", "url"),
        }
        if exact:
            exact.update({k: v for k, v in resolved.items() if v and not exact.get(k)})
        else:
            albums = [resolved, *albums]
        return albums, artists, tracks, collection_hits, search_term

    return [], [], [], [], query


async def build_search_context(request, query: str) -> dict:
    albums = []
    artists = []
    tracks = []
    collection_hits = []
    search_term = query
    clean_query = (query or "").strip()

    if clean_query and len(clean_query) >= 2:
        if is_url(clean_query):
            albums, artists, tracks, collection_hits, search_term = await _run_url_search(clean_query)
        else:
            albums, artists, tracks, collection_hits = await _run_text_search(clean_query)

    top_result = await enrich_top_result(
        _pick_top_result(search_term, artists, collection_hits, albums, tracks)
    )

    return {
        "request": request,
        "results": albums,
        "artists": _artist_results_for_query(search_term, top_result, artists),
        "track_results": tracks,
        "collection_hits": collection_hits,
        "top_result": top_result,
        "query": query,
        "search_term": search_term,
        "url_not_supported": is_url(clean_query) and not (top_result or albums or artists or tracks or collection_hits),
    }
