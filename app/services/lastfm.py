import asyncio
import httpx
from app.config import settings
from app.utils import normalize_album

_mb_client = None

def _get_mb_client():
    global _mb_client
    if _mb_client is None:
        _mb_client = httpx.AsyncClient(timeout=8.0, follow_redirects=True, verify=True)
    return _mb_client


async def get_artist_image(artist: str) -> str | None:
    """Try Deezer then iTunes for an artist image URL."""
    client = _get_mb_client()
    # 1. Deezer
    try:
        r = await client.get("https://api.deezer.com/search/artist", params={"q": artist, "limit": 1})
        data = r.json().get("data", [])
        if data and data[0].get("picture_xl"):
            return data[0]["picture_xl"]
    except Exception:
        pass
    # 2. iTunes
    try:
        r = await client.get("https://itunes.apple.com/search", params={"term": artist, "entity": "musicArtist", "limit": 1})
        results = r.json().get("results", [])
        if results and results[0].get("artworkUrl100"):
            return results[0]["artworkUrl100"].replace("100x100", "600x600")
    except Exception:
        pass
    return None


async def get_deezer_album_info(deezer_id: str) -> dict | None:
    """Fetch full album info directly from Deezer by album ID."""
    if not deezer_id:
        return None
    try:
        import re as _re
        client = _get_mb_client()
        album_r, tracks_r = await asyncio.gather(
            client.get(f"https://api.deezer.com/album/{deezer_id}"),
            client.get(f"https://api.deezer.com/album/{deezer_id}/tracks?limit=100"),
        )
        a = album_r.json()
        if a.get("error"):
            return None
        raw_tracks = tracks_r.json().get("data", [])
        tracks = []
        for i, t in enumerate(raw_tracks, 1):
            dur = int(t.get("duration") or 0)
            tracks.append({
                "rank": i,
                "name": t.get("title", ""),
                "duration": f"{dur//60}:{dur%60:02d}" if dur else "",
                "preview": t.get("preview", ""),
            })
        copyright_raw = a.get("label", "") or ""
        release_date = a.get("release_date", "")
        cover = a.get("cover_xl") or a.get("cover_big") or a.get("cover", "")
        genre_data = a.get("genres", {}).get("data", [])
        genre = genre_data[0].get("name", "") if genre_data else ""
        return {
            "artist": a.get("artist", {}).get("name", ""),
            "album": a.get("title", ""),
            "cover_url": cover,
            "tracks": tracks,
            "release_date": release_date,
            "year": release_date[:4] if release_date else "",
            "label": copyright_raw,
            "genre": genre,
            "track_count": len(tracks),
        }
    except Exception:
        return None

async def get_mb_release_date(mb_id: str) -> str:
    """Fetch release date from MusicBrainz for a given release MBID."""
    if not mb_id:
        return ""
    try:
        r = await _get_mb_client().get(
            f"https://musicbrainz.org/ws/2/release/{mb_id}",
            params={"fmt": "json"},
            headers={"User-Agent": "Redwave/1.0 (redwave@example.com)"},
        )
        if r.status_code == 200:
            return r.json().get("date", "")
    except Exception:
        pass
    return ""


_TYPE_ORDER = ["Album", "EP", "Single", "Broadcast", "Other"]

def _rg_label(rg: dict) -> str:
    primary = rg.get("primary-type") or "Other"
    secondary = rg.get("secondary-types") or []
    if secondary:
        return f"{primary} + {', '.join(secondary)}"
    return primary


async def _mb_search_artist_id(artist_name: str, client: httpx.AsyncClient) -> str:
    """Search MusicBrainz for an artist by name, return MBID of best match.
    Tries exact name, plain search, and reversed name order (for non-Western naming)."""
    parts = artist_name.strip().split()
    candidates = [
        f'artist:"{artist_name}"',
        artist_name,
    ]
    if len(parts) == 2:
        reversed_name = f"{parts[1]} {parts[0]}"
        candidates.append(f'artist:"{reversed_name}"')
        candidates.append(reversed_name)

    for query in candidates:
        try:
            r = await client.get(
                "https://musicbrainz.org/ws/2/artist",
                params={"query": query, "fmt": "json", "limit": 5},
                headers={"User-Agent": settings.musicbrainz_user_agent},
            )
            if r.status_code == 200:
                artists = r.json().get("artists", [])
                if artists:
                    return artists[0].get("id", "")
            await asyncio.sleep(1.1)
        except Exception:
            pass
    return ""


async def _mb_fetch_release_groups(artist_mbid: str, client: httpx.AsyncClient) -> list[dict]:
    all_rgs = []
    offset = 0
    limit = 100
    while True:
        r = await client.get(
            "https://musicbrainz.org/ws/2/release-group",
            params={"artist": artist_mbid, "fmt": "json", "limit": limit, "offset": offset},
            headers={"User-Agent": settings.musicbrainz_user_agent},
        )
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("release-groups", [])
        all_rgs.extend(batch)
        total = data.get("release-group-count", 0)
        if not batch or len(all_rgs) >= total or len(batch) < limit:
            break
        offset += limit
        await asyncio.sleep(1.1)
    return all_rgs


async def get_mb_discography(artist_mbid: str, artist_name: str = "") -> dict[str, list[dict]]:
    """Fetch all release groups for an artist from MusicBrainz, grouped by type."""
    all_rgs = []
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=True) as client:
            if artist_mbid:
                all_rgs = await _mb_fetch_release_groups(artist_mbid, client)
            if len(all_rgs) < 3 and artist_name:
                await asyncio.sleep(1.1)
                found_id = await _mb_search_artist_id(artist_name, client)
                if found_id and found_id != artist_mbid:
                    await asyncio.sleep(1.1)
                    candidate_rgs = await _mb_fetch_release_groups(found_id, client)
                    if len(candidate_rgs) > len(all_rgs):
                        all_rgs = candidate_rgs
    except Exception:
        pass

    # Group and sort
    grouped: dict[str, list[dict]] = {}
    for rg in all_rgs:
        label = _rg_label(rg)
        date = rg.get("first-release-date", "") or ""
        year = date[:4] if date else ""
        grouped.setdefault(label, []).append({
            "mb_id": rg.get("id", ""),
            "album": rg.get("title", ""),
            "year": year,
            "cover_url": f"https://coverartarchive.org/release-group/{rg['id']}/front-250" if rg.get("id") else "",
            "primary_type": rg.get("primary-type") or "Other",
        })

    # Sort each group by year
    for label in grouped:
        grouped[label].sort(key=lambda x: x["year"] or "9999")

    # Return in logical type order
    ordered = {}
    seen_labels = set()
    for primary in _TYPE_ORDER:
        for label in sorted(grouped.keys()):
            if label not in seen_labels and (label == primary or label.startswith(primary + " +")):
                ordered[label] = grouped[label]
                seen_labels.add(label)
    for label in grouped:
        if label not in seen_labels:
            ordered[label] = grouped[label]

    return ordered


async def _tracks_from_mb(mb_id: str) -> list[dict]:
    if not mb_id:
        return []
    try:
        r = await _get_mb_client().get(
            f"https://musicbrainz.org/ws/2/release/{mb_id}",
            params={"fmt": "json", "inc": "recordings"},
            headers={"User-Agent": "Redwave/1.0 (redwave@example.com)"},
        )
        if r.status_code != 200:
            return []
        tracks = []
        for medium in r.json().get("media", []):
            for t in medium.get("tracks", []):
                dur_ms = t.get("length") or 0
                dur_s = dur_ms // 1000
                tracks.append({
                    "rank": t.get("position", len(tracks) + 1),
                    "name": t.get("title", ""),
                    "duration": f"{dur_s//60}:{dur_s%60:02d}" if dur_s else "",
                })
        return tracks
    except Exception:
        return []


async def _cover_from_discogs(artist: str, album: str) -> str | None:
    try:
        client = _get_mb_client()
        headers = {"User-Agent": "Redwave/1.0"}
        if settings.discogs_token:
            headers["Authorization"] = f"Discogs token={settings.discogs_token}"
        for kind in ("master", "release"):
            r = await client.get(
                "https://api.discogs.com/database/search",
                params={"artist": artist, "release_title": album, "type": kind, "per_page": 1},
                headers=headers,
            )
            results = r.json().get("results", [])
            if results:
                img = results[0].get("cover_image") or results[0].get("thumb")
                if img and "spacer" not in img:
                    return img
    except Exception:
        pass
    return None


async def _tracks_from_discogs(artist: str, album: str) -> list[dict]:
    try:
        client = _get_mb_client()
        for kind in ("master", "release"):
            r = await client.get(
                "https://api.discogs.com/database/search",
                params={"artist": artist, "release_title": album, "type": kind, "per_page": 1},
                headers={"User-Agent": "Redwave/1.0"},
            )
            results = r.json().get("results", [])
            if results:
                break
        if not results:
            return []
        detail = await client.get(results[0]["resource_url"], headers={"User-Agent": "Redwave/1.0"})
        tracks = []
        for t in detail.json().get("tracklist", []):
            if t.get("type_") == "heading":
                continue
            tracks.append({
                "rank": t.get("position", len(tracks) + 1),
                "name": t.get("title", ""),
                "duration": t.get("duration", ""),
            })
        return tracks
    except Exception:
        return []


async def _tracks_from_deezer(artist: str, album: str) -> list[dict]:
    try:
        import urllib.parse as _up
        client = _get_mb_client()
        r = await client.get(
            f"https://api.deezer.com/search/album?q={_up.quote(artist + ' ' + album)}&limit=5"
        )
        items = r.json().get("data", [])
        if not items:
            return []
        # Pick the result whose title and artist best match — don't blindly take index 0
        album_lower = album.lower()
        artist_lower = artist.lower()
        def _dz_score(i):
            t = i.get("title", "").lower()
            a = i.get("artist", {}).get("name", "").lower()
            return (t == album_lower, album_lower in t or t in album_lower, artist_lower in a or a in artist_lower)
        best = max(items, key=_dz_score)
        # Reject if neither the title nor artist match at all
        title_match = album_lower in best.get("title", "").lower() or best.get("title", "").lower() in album_lower
        artist_match = artist_lower in best.get("artist", {}).get("name", "").lower()
        if not title_match and not artist_match:
            return []
        album_id = best.get("id")
        tr = await client.get(f"https://api.deezer.com/album/{album_id}/tracks?limit=100")
        tracks = []
        for i, t in enumerate(tr.json().get("data", []), 1):
            dur = int(t.get("duration") or 0)
            tracks.append({
                "rank": i,
                "name": t.get("title", ""),
                "duration": f"{dur//60}:{dur%60:02d}" if dur else "",
                "preview": t.get("preview", ""),
            })
        return tracks
    except Exception:
        return []


async def _tracks_from_itunes(artist: str, album: str) -> list[dict]:
    try:
        import urllib.parse as _up
        client = _get_mb_client()
        r = await client.get(
            f"https://itunes.apple.com/search?term={_up.quote(artist + ' ' + album)}&entity=album&limit=10"
        )
        import re as _re2
        _junk = _re2.compile(r'\b(karaoke|tribute|cover version|instrumental|made famous|originally performed|backing track|sing.?along)\b', _re2.IGNORECASE)
        all_results = r.json().get("results", [])
        albums = [a for a in all_results if a.get("wrapperType") == "collection" and not _junk.search(a.get("collectionName","")) and not _junk.search(a.get("artistName",""))]
        if not albums:
            return []
        album_lower = album.lower()
        artist_lower = artist.lower()
        def _score(i):
            t = i.get("collectionName", "").lower()
            a = i.get("artistName", "").lower()
            return (t == album_lower, album_lower in t or t in album_lower, artist_lower in a or a in artist_lower)
        best = max(albums, key=_score)
        cid = best["collectionId"]
        tr = await client.get(f"https://itunes.apple.com/lookup?id={cid}&entity=song")
        tracks = [
            {
                "rank": t.get("trackNumber", i),
                "name": t.get("trackName", ""),
                "duration": (lambda ms: f"{ms//60000}:{(ms%60000)//1000:02d}" if ms else "")(t.get("trackTimeMillis") or 0),
            }
            for i, t in enumerate(tr.json().get("results", []), 1)
            if t.get("wrapperType") == "track"
        ]
        tracks.sort(key=lambda x: x["rank"])
        return tracks
    except Exception:
        return []


async def get_tracklist_with_fallback(
    lfm_tracks: list, artist: str, album: str, mb_id: str = "", deezer_id: str = ""
) -> list[dict]:
    """Return tracklist using priority order: MB > Deezer > Last.fm > Discogs > iTunes."""
    mb, deezer_search, discogs, deezer_direct, itunes = await asyncio.gather(
        _tracks_from_mb(mb_id),
        _tracks_from_deezer(artist, album),
        _tracks_from_discogs(artist, album),
        (get_deezer_album_info(deezer_id) if deezer_id else asyncio.sleep(0)),
        _tracks_from_itunes(artist, album),
    )

    deezer_direct_tracks = (deezer_direct or {}).get("tracks", []) if isinstance(deezer_direct, dict) else []

    # Priority: MB (exact ID) > Deezer (accurate for recent releases) >
    # Last.fm > Discogs > iTunes (text searches, least reliable)
    for source in (mb, deezer_search, deezer_direct_tracks, lfm_tracks, discogs, itunes):
        if source:
            return source
    return []


class LastFmClient:
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get_artist_info(self, artist: str) -> dict | None:
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "artist.getinfo",
                "artist": artist,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "autocorrect": 1,
            })
            r.raise_for_status()
            a = r.json().get("artist", {})
            if not a:
                return None
            image = next(
                (img["#text"] for img in a.get("image", [])
                 if img["size"] == "extralarge" and img["#text"]
                 and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                None,
            )
            bio = a.get("bio", {}).get("summary", "")
            # Strip Last.fm "Read more" link from bio
            if "<a href" in bio:
                bio = bio[:bio.index("<a href")].strip()
            # Strip featured artist credits from similar artist names
            _ft_re = __import__('re').compile(r'\s+(ft\.?|feat\.?|featuring)\s+.+$', __import__('re').IGNORECASE)
            similar = [
                {"name": _ft_re.sub("", s["name"]).strip(), "url": s["url"]}
                for s in a.get("similar", {}).get("artist", [])
            ]
            tags = [t["name"] for t in a.get("tags", {}).get("tag", [])]
            return {
                "name": a.get("name", artist),
                "image": image,
                "listeners": int(a.get("stats", {}).get("listeners", 0)),
                "playcount": int(a.get("stats", {}).get("playcount", 0)),
                "bio": bio,
                "similar": similar,
                "tags": tags,
                "url": a.get("url", ""),
                "mb_id": a.get("mbid", ""),
            }
        except Exception:
            return None

    async def get_artist_top_albums(self, artist: str, limit: int = 12) -> list[dict]:
        # Generic/fake "album" names that users create on Last.fm
        _JUNK_ALBUMS = {
            "download", "leaks", "single", "singles", "music", "video", "videos",
            "playlist", "various", "unknown", "misc", "b-sides", "demos", "bootleg",
            "bootlegs", "mixtape", "ep", "ost", "soundtrack", "samsung", "amazon",
            "spotify", "apple", "tidal", "soundcloud", "youtube", "vevo",
        }
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "artist.gettopalbums",
                "artist": artist,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit * 2,
                "autocorrect": 1,
            })
            r.raise_for_status()
            albums = r.json().get("topalbums", {}).get("album", [])
            out = []
            for a in albums:
                name = a.get("name", "")
                if not name or name == "(null)":
                    continue
                # Filter obvious junk: exact match to generic words, or just the artist name
                name_lower = name.lower().strip()
                if name_lower in _JUNK_ALBUMS:
                    continue
                if name_lower == artist.lower().strip():
                    continue
                # Filter entries with no mb_id and suspiciously low playcount (likely fake)
                mb_id = a.get("mbid", "")
                playcount = int(a.get("playcount", 0))
                if not mb_id and playcount < 100:
                    continue
                cover = next(
                    (img["#text"] for img in a.get("image", [])
                     if img["size"] == "extralarge" and img["#text"]
                     and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                    None,
                )
                if not cover and mb_id:
                    cover = f"https://coverartarchive.org/release-group/{mb_id}/front-250"
                out.append({
                    "album": name,
                    "artist": artist,
                    "mb_id": mb_id,
                    "cover_url": cover,
                    "playcount": playcount,
                })
            # Deduplicate: keep first (highest playcount) per normalized title
            seen: set[str] = set()
            deduped = []
            for a in out:
                key = normalize_album(a["album"]).lower()
                if key not in seen:
                    seen.add(key)
                    deduped.append(a)
            return deduped[:limit]
        except Exception:
            return []

    async def get_top_albums(self, period: str = "7day", limit: int = 12) -> list[dict]:
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "user.gettopalbums",
                "user": settings.lastfm_username,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "period": period,
                "limit": limit,
            })
            if not r.is_success:
                return []
        except Exception:
            return []
        data = r.json()
        albums = data.get("topalbums", {}).get("album", [])
        return [
            {
                "artist": a["artist"]["name"],
                "album": a["name"],
                "cover_url": next((img["#text"] for img in a.get("image", []) if img["size"] == "extralarge"), None),
                "playcount": a.get("playcount", 0),
                "url": a.get("url", ""),
            }
            for a in albums
        ]

    async def get_recent_tracks(self, limit: int = 10) -> list[dict]:
        r = await self._client.get(self.BASE_URL, params={
            "method": "user.getrecenttracks",
            "user": settings.lastfm_username,
            "api_key": settings.lastfm_api_key,
            "format": "json",
            "limit": limit,
        })
        r.raise_for_status()
        data = r.json()
        return data.get("recenttracks", {}).get("track", [])

    async def get_artist_top_tracks_global(self, artist: str, limit: int = 50) -> dict[str, int]:
        """Returns {track_name_lower: rank} for an artist's globally most played tracks."""
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "artist.gettoptracks",
                "artist": artist,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit,
                "autocorrect": 1,
            })
            r.raise_for_status()
            tracks = r.json().get("toptracks", {}).get("track", [])
            # rank 1 = most popular; invert so higher = better
            return {t["name"].lower(): int(t.get("playcount", 0)) for t in tracks if t.get("name")}
        except Exception:
            return {}

    async def get_top_tracks_lookup(self, limit: int = 500) -> dict[str, int]:
        """Returns {track_name_lower: playcount} for the user's top tracks."""
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "user.gettoptracks",
                "user": settings.lastfm_username,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit,
                "period": "overall",
            })
            r.raise_for_status()
            tracks = r.json().get("toptracks", {}).get("track", [])
            return {t["name"].lower(): int(t.get("playcount", 0)) for t in tracks if t.get("name")}
        except Exception:
            return {}

    async def get_album_info(self, artist: str, album: str) -> dict | None:
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "album.getinfo",
                "artist": artist,
                "album": album,
                "api_key": settings.lastfm_api_key,
                "format": "json",
            })
            r.raise_for_status()
            data = r.json()
            a = data.get("album", {})
            if not a:
                return None
            cover_url = next((img["#text"] for img in a.get("image", []) if img["size"] == "extralarge" and img["#text"]), None)
            raw_tracks = a.get("tracks", {}).get("track", [])
            if isinstance(raw_tracks, dict):
                raw_tracks = [raw_tracks]
            tracks = []
            for t in raw_tracks:
                dur = int(t.get("duration") or 0)
                tracks.append({
                    "rank": t.get("@attr", {}).get("rank", ""),
                    "name": t.get("name", ""),
                    "duration": f"{dur//60}:{dur%60:02d}" if dur else "",
                })
            return {
                "artist": a.get("artist", artist),
                "album": a.get("name", album),
                "cover_url": cover_url,
                "mb_id": a.get("mbid", ""),
                "tags": [t["name"] for t in a.get("tags", {}).get("tag", [])],
                "tracks": tracks,
                "listeners": int(a.get("listeners") or 0),
                "playcount": int(a.get("playcount") or 0),
            }
        except Exception:
            return None

    async def get_itunes_info(self, artist: str, album: str) -> dict | None:
        try:
            import urllib.parse
            q = urllib.parse.quote(f"{artist} {album}")
            r = await self._client.get(
                f"https://itunes.apple.com/search?term={q}&entity=album&limit=25"
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            import re as _re
            _junk_re = _re.compile(
                r'\b(karaoke|tribute|cover version|instrumental|made famous|originally performed|backing track|sing.?along)\b',
                _re.IGNORECASE,
            )
            albums = [
                i for i in results
                if (i.get("wrapperType") == "collection" or i.get("collectionType") == "Album")
                and not _junk_re.search(i.get("collectionName", ""))
                and not _junk_re.search(i.get("artistName", ""))
            ]
            if not albums:
                albums = [i for i in results if not _junk_re.search(i.get("collectionName", ""))]
            if not albums:
                albums = results
            # Prefer the closest title match to avoid picking wrong albums
            album_lower = album.lower()
            artist_lower = artist.lower()
            def _match_score(i):
                t = i.get("collectionName", "").lower()
                a = i.get("artistName", "").lower()
                exact = t == album_lower
                title_contains = album_lower in t or t in album_lower
                artist_match = artist_lower in a or a in artist_lower
                return (exact, title_contains, artist_match)
            item = max(albums, key=_match_score, default=None)
            if item:
                    raw_date = item.get("releaseDate", "")
                    year = raw_date[:4] if raw_date else ""
                    copyright_raw = item.get("copyright", "")
                    label = _re.sub(r'^[℗©]\s*\d{4}\s*', '', copyright_raw).strip()
                    collection_type = item.get("collectionType", "Album")
                    release_type = "Single" if collection_type == "Single" else "EP" if collection_type == "EP" else "Album"
                    return {
                        "release_date": raw_date[:10] if raw_date else "",
                        "year": year,
                        "itunes_url": item.get("collectionViewUrl", ""),
                        "label": label,
                        "track_count": item.get("trackCount", 0),
                        "genre": item.get("primaryGenreName", ""),
                        "release_type": release_type,
                    }
        except Exception:
            pass
        return None

    async def search_albums(self, query: str, limit: int = 20) -> list[dict]:
        r = await self._client.get(self.BASE_URL, params={
            "method": "album.search",
            "album": query,
            "api_key": settings.lastfm_api_key,
            "format": "json",
            "limit": limit,
        })
        r.raise_for_status()
        data = r.json()
        albums = data.get("results", {}).get("albummatches", {}).get("album", [])
        return [
            {
                "mb_id": a.get("mbid", ""),
                "artist": a.get("artist", ""),
                "album": a.get("name", ""),
                "cover_url": next((img["#text"] for img in a.get("image", []) if img["size"] == "extralarge" and img["#text"]), None),
            }
            for a in albums
        ]

    async def search_artists(self, query: str, limit: int = 5) -> list[dict]:
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "artist.search",
                "artist": query,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit,
            })
            r.raise_for_status()
            artists = r.json().get("results", {}).get("artistmatches", {}).get("artist", [])
            out = []
            for a in artists:
                image = next(
                    (img["#text"] for img in a.get("image", [])
                     if img["size"] == "extralarge" and img["#text"]
                     and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                    None,
                )
                out.append({
                    "name": a.get("name", ""),
                    "listeners": int(a.get("listeners", 0)),
                    "mb_id": a.get("mbid", ""),
                    "image": image,
                })
            return out
        except Exception:
            return []

    async def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        """Search tracks, resolve each to its album via track.getInfo."""
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "track.search",
                "track": query,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit,
            })
            r.raise_for_status()
            tracks = r.json().get("results", {}).get("trackmatches", {}).get("track", [])
        except Exception:
            return []

        def _clean_album_title(title: str) -> str:
            """Strip edition/version tags like [Clean], [Explicit], (Deluxe Edition) etc."""
            import re as _re
            return _re.sub(
                r'\s*[\(\[][^\)\]]*\b(clean|explicit|deluxe|edition|version|remaster|remastered|bonus|expanded)\b[^\)\]]*[\)\]]',
                '', title, flags=_re.IGNORECASE
            ).strip()

        async def _mb_recording(artist: str, track_name: str) -> dict | None:
            """Look up a recording on MusicBrainz to find the real album title."""
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, verify=True) as mb:
                    r = await mb.get(
                        "https://musicbrainz.org/ws/2/recording",
                        params={
                            "query": f'recording:"{track_name}" AND artist:"{artist}"',
                            "fmt": "json",
                            "limit": 5,
                        },
                        headers={"User-Agent": settings.musicbrainz_user_agent},
                    )
                    recordings = r.json().get("recordings", [])
                    for rec in recordings:
                        releases = rec.get("releases", [])
                        for rel in releases:
                            rg = rel.get("release-group", {})
                            pt = (rg.get("primary-type") or "").lower()
                            if pt in ("album", "ep"):
                                return {
                                    "album": _clean_album_title(rel.get("title", "")),
                                    "mb_id": rg.get("id", ""),
                                    "cover_url": f"https://coverartarchive.org/release-group/{rg['id']}/front-250" if rg.get("id") else None,
                                }
                        if releases:
                            rel = releases[0]
                            rg = rel.get("release-group", {})
                            return {
                                "album": _clean_album_title(rel.get("title", "")),
                                "mb_id": rg.get("id", ""),
                                "cover_url": f"https://coverartarchive.org/release-group/{rg['id']}/front-250" if rg.get("id") else None,
                            }
            except Exception:
                pass
            return None

        async def _resolve(t):
            artist = t.get("artist", "")
            track_name = t.get("name", "")
            try:
                ri = await self._client.get(self.BASE_URL, params={
                    "method": "track.getInfo",
                    "artist": artist,
                    "track": track_name,
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                })
                info = ri.json().get("track", {})
                album = info.get("album", {})
                album_title = album.get("title", "")
                album_mbid = album.get("mbid", "")
                cover = next(
                    (img["#text"] for img in album.get("image", [])
                     if img["size"] == "extralarge" and img["#text"]
                     and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                    None,
                )
                # Clean edition tags from Last.fm album title
                if album_title:
                    album_title = _clean_album_title(album_title)
                # If album title still matches track name it's a fake single — try MB
                def _slug(s: str) -> str:
                    import re as _re
                    return _re.sub(r'[^a-z0-9]', '', s.lower())
                if not album_title or _slug(album_title) == _slug(track_name):
                    mb = await _mb_recording(artist, track_name)
                    if mb and mb["album"]:
                        album_title = mb["album"]
                        album_mbid = mb["mb_id"]
                        cover = cover or mb["cover_url"]
                if not album_title:
                    return None
                return {
                    "track": track_name,
                    "artist": artist,
                    "album": album_title,
                    "mb_id": album_mbid,
                    "cover_url": cover,
                }
            except Exception:
                return None

        results = await asyncio.gather(*[_resolve(t) for t in tracks])
        # Deduplicate by album, keep first occurrence
        seen = set()
        out = []
        for r in results:
            if not r:
                continue
            key = f"{r['artist'].lower()}|{r['album'].lower()}"
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    async def get_recommended_artists(self, limit: int = 12) -> list[dict]:
        r = await self._client.get(self.BASE_URL, params={
            "method": "user.gettopartists",
            "user": settings.lastfm_username,
            "api_key": settings.lastfm_api_key,
            "format": "json",
            "period": "1month",
            "limit": limit,
        })
        r.raise_for_status()
        data = r.json()
        return data.get("topartists", {}).get("artist", [])

    async def _get_similar_artists(self, artist: str, limit: int = 5) -> list[str]:
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "artist.getsimilar",
                "artist": artist,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit,
            })
            r.raise_for_status()
            artists = r.json().get("similarartists", {}).get("artist", [])
            return [a["name"] for a in artists]
        except Exception:
            return []

    async def _get_artist_top_albums(self, artist: str, limit: int = 3) -> list[dict]:
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "artist.gettopalbums",
                "artist": artist,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": limit,
            })
            r.raise_for_status()
            albums = r.json().get("topalbums", {}).get("album", [])
            out = []
            for a in albums:
                if not a.get("name") or a.get("name") == "(null)":
                    continue
                cover = next(
                    (img["#text"] for img in a.get("image", [])
                     if img["size"] == "extralarge" and img["#text"]
                     and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                    None,
                )
                out.append({
                    "artist": a.get("artist", {}).get("name", artist),
                    "album": a.get("name", ""),
                    "mb_id": a.get("mbid", ""),
                    "cover_url": cover,
                    "playcount": a.get("playcount", 0),
                })
            return out
        except Exception:
            return []

    async def get_recommended_tracks(self, limit: int = 18) -> list[dict]:
        """Get similar tracks based on the user's top tracks."""
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "user.gettoptracks",
                "user": settings.lastfm_username,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "period": "1month",
                "limit": 5,
            })
            top_tracks = r.json().get("toptracks", {}).get("track", [])
        except Exception:
            return []

        async def _get_similar(track_name, artist_name):
            try:
                r = await self._client.get(self.BASE_URL, params={
                    "method": "track.getsimilar",
                    "track": track_name,
                    "artist": artist_name,
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                    "limit": 6,
                })
                return r.json().get("similartracks", {}).get("track", [])
            except Exception:
                return []

        similar_lists = await asyncio.gather(*[
            _get_similar(t["name"], t["artist"]["name"]) for t in top_tracks
        ])

        known = {f"{t['name'].lower()}|{t['artist']['name'].lower()}" for t in top_tracks}
        seen = set()
        results = []
        for similar in similar_lists:
            for t in similar:
                key = f"{t.get('name','').lower()}|{t.get('artist',{}).get('name','').lower()}"
                if key in known or key in seen:
                    continue
                seen.add(key)
                images = t.get("image", [])
                cover = next(
                    (img["#text"] for img in images
                     if img["size"] == "extralarge" and img["#text"]
                     and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                    None,
                )
                results.append({
                    "title": t.get("name", ""),
                    "artist": t.get("artist", {}).get("name", ""),
                    "cover_url": cover,
                    "mb_id": t.get("mbid", ""),
                    "duration": (lambda d: f"{int(d)//60}:{int(d)%60:02d}" if d else "")(t.get("duration", "")),
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        return results

    async def enrich_tracks_with_covers(self, tracks: list[dict]) -> list[dict]:
        """Add cover_url to each track dict using Last.fm → CAA → Deezer fallback chain."""
        client = _get_mb_client()

        async def _deezer_cover(artist: str, album: str) -> str | None:
            try:
                import urllib.parse as _up
                r = await client.get(
                    f"https://api.deezer.com/search/album?q={_up.quote(artist + ' ' + album)}&limit=3"
                )
                items = r.json().get("data", [])
                if not items:
                    return None
                album_lower = album.lower()
                artist_lower = artist.lower()
                best = max(items, key=lambda i: (
                    i.get("title", "").lower() == album_lower,
                    album_lower in i.get("title", "").lower(),
                    artist_lower in i.get("artist", {}).get("name", "").lower(),
                ))
                return best.get("cover_big") or best.get("cover_xl") or best.get("cover")
            except Exception:
                return None

        async def _lfm_cover(artist: str, album: str) -> str | None:
            """Look up cover via Last.fm album.getinfo by artist+album name."""
            try:
                r = await self._client.get(self.BASE_URL, params={
                    "method": "album.getinfo",
                    "artist": artist,
                    "album": album,
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                })
                images = r.json().get("album", {}).get("image", [])
                return next(
                    (img["#text"] for img in reversed(images)
                     if img["#text"] and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                    None,
                )
            except Exception:
                return None

        async def _itunes_cover(artist: str, album: str) -> str | None:
            """Look up cover via iTunes Search API — free, no auth, good non-English coverage."""
            try:
                import urllib.parse as _up
                r = await client.get(
                    "https://itunes.apple.com/search",
                    params={"term": f"{artist} {album}", "entity": "album", "limit": 5},
                )
                results = r.json().get("results", [])
                if not results:
                    return None
                album_lower = album.lower()
                artist_lower = artist.lower()
                best = max(results, key=lambda i: (
                    i.get("collectionName", "").lower() == album_lower,
                    album_lower in i.get("collectionName", "").lower(),
                    artist_lower in i.get("artistName", "").lower(),
                ))
                url = best.get("artworkUrl100", "")
                # Upgrade to 500px
                return url.replace("100x100bb", "500x500bb") if url else None
            except Exception:
                return None

        def _primary_artist(name: str) -> str:
            """Strip feat./vs./meets suffixes so lookups use the main artist name."""
            import re as _re
            return _re.split(r'\s+(?:feat\.?|ft\.?|vs\.?|meets?|x\b)', name, flags=_re.IGNORECASE)[0].strip()

        async def _fetch(t):
            try:
                artist = t.get("artist", "")
                album_title = t.get("album", "")
                album_mbid = t.get("release_mbid", "")
                main_artist = _primary_artist(artist)

                # Step 1: Last.fm album.getinfo (best source when album title is known)
                cover = None
                if artist and album_title:
                    cover = await _lfm_cover(artist, album_title)
                # Retry with main artist name if feat. was stripped
                if not cover and main_artist != artist and album_title:
                    cover = await _lfm_cover(main_artist, album_title)

                # Step 2: Fall back to track.getinfo to also resolve album title/mbid
                if not cover or not album_title or not album_mbid:
                    try:
                        r = await self._client.get(self.BASE_URL, params={
                            "method": "track.getinfo",
                            "artist": artist,
                            "track": t.get("title", ""),
                            "api_key": settings.lastfm_api_key,
                            "format": "json",
                        })
                        lfm_album = r.json().get("track", {}).get("album", {})
                        if not album_title:
                            album_title = lfm_album.get("title", "")
                        if not album_mbid:
                            album_mbid = lfm_album.get("mbid", "")
                        if not cover:
                            images = lfm_album.get("image", [])
                            cover = next(
                                (img["#text"] for img in images
                                 if img["size"] == "extralarge" and img["#text"]
                                 and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                                None,
                            )
                    except Exception:
                        pass

                # Step 3: CoverArtArchive by release MBID
                if not cover and album_mbid:
                    cover = f"https://coverartarchive.org/release/{album_mbid}/front-250"
                # Step 4: Deezer search (try main artist if full name failed)
                if not cover and album_title and artist:
                    cover = await _deezer_cover(main_artist, album_title)
                # Step 5: Discogs (good for niche/electronic/vinyl releases)
                if not cover and album_title and artist:
                    cover = await _cover_from_discogs(main_artist, album_title)
                # Step 6: iTunes (broad coverage including non-English releases)
                if not cover and album_title and artist:
                    cover = await _itunes_cover(main_artist, album_title)

                return {**t, "cover_url": cover, "album": album_title, "album_mbid": album_mbid}
            except Exception:
                return {**t, "cover_url": None}

        return list(await asyncio.gather(*[_fetch(t) for t in tracks]))

    def _sign(self, params: dict) -> str:
        """Compute Last.fm API signature (MD5 of sorted params + shared secret)."""
        import hashlib
        secret = settings.lastfm_shared_secret.strip()
        sig_str = "".join(f"{k}{v}" for k, v in sorted(params.items())) + secret
        return hashlib.md5(sig_str.encode("utf-8")).hexdigest()

    async def _auth_get(self, method: str, extra: dict | None = None) -> dict:
        """Make an authenticated (session-key signed) Last.fm API GET call."""
        params = {
            "method": method,
            "api_key": settings.lastfm_api_key.strip(),
            "sk": settings.lastfm_session_key.strip(),
        }
        if extra:
            params.update(extra)
        params["api_sig"] = self._sign(params)
        params["format"] = "json"
        r = await self._client.get(self.BASE_URL, params=params)
        r.raise_for_status()
        return r.json()

    async def _real_recommended_artists(self, limit: int = 20) -> list[str]:
        """Last.fm's own recommendation engine — requires session key."""
        try:
            data = await self._auth_get("user.getRecommendedArtists", {"limit": limit})
            artists = data.get("recommendations", {}).get("artist", [])
            return [a["name"] for a in artists if a.get("name")]
        except Exception:
            return []

    async def get_weekly_recommendations(self, limit: int = 18) -> list[dict]:
        """Build album recommendations from user's top artists → similar artists → their top albums."""
        import random
        top_artists = await self.get_recommended_artists(limit=20)
        artist_names = [a["name"] for a in top_artists]
        similar_lists = await asyncio.gather(*[
            self._get_similar_artists(name, limit=6) for name in artist_names
        ])
        known = {n.lower() for n in artist_names}
        candidates = []
        for similar in similar_lists:
            for name in similar:
                if name.lower() not in known and name not in candidates:
                    candidates.append(name)

        # Shuffle so each cache refresh gives different results
        random.shuffle(candidates)

        # Get top album from each candidate artist in parallel
        album_lists = await asyncio.gather(*[
            self._get_artist_top_albums(name, limit=3) for name in candidates[:limit]
        ])

        recommendations = []
        seen_albums = set()
        seen_artists = set()
        for albums in album_lists:
            for alb in albums:
                artist_key = alb['artist'].lower()
                album_key = f"{artist_key}|{alb['album'].lower()}"
                if artist_key not in seen_artists and album_key not in seen_albums:
                    seen_artists.add(artist_key)
                    seen_albums.add(album_key)
                    recommendations.append(alb)
                    break
            if len(recommendations) >= limit:
                break

        return recommendations


    async def get_tag_recommendations(self, limit: int = 12) -> list[dict]:
        """Get album recommendations based on user's top tags."""
        try:
            # Get user's top tags
            r = await self._client.get(self.BASE_URL, params={
                "method": "user.getTopTags",
                "user": settings.lastfm_username,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": 8,
            })
            tags = r.json().get("toptags", {}).get("tag", [])
            tag_names = [t["name"] for t in tags if int(t.get("count", 0)) > 1][:5]
        except Exception:
            return []

        # Get top albums for each tag in parallel
        async def _tag_albums(tag: str) -> list[dict]:
            try:
                r = await self._client.get(self.BASE_URL, params={
                    "method": "tag.getTopAlbums",
                    "tag": tag,
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                    "limit": 10,
                })
                albums = r.json().get("albums", {}).get("album", [])
                return [{
                    "artist": a.get("artist", {}).get("name", ""),
                    "album": a.get("name", ""),
                    "mb_id": a.get("mbid", ""),
                    "cover_url": next(
                        (img["#text"] for img in a.get("image", [])
                         if img["size"] == "extralarge" and img["#text"]
                         and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                        None,
                    ),
                    "tag": tag,
                } for a in albums if a.get("artist", {}).get("name") and a.get("name")]
            except Exception:
                return []

        tag_results = await asyncio.gather(*[_tag_albums(t) for t in tag_names])

        # Interleave results from different tags, deduplicate
        seen = set()
        results = []
        for i in range(10):
            for tag_list in tag_results:
                if i < len(tag_list):
                    item = tag_list[i]
                    key = f"{item['artist'].lower()}|{item['album'].lower()}"
                    if key not in seen:
                        seen.add(key)
                        results.append(item)
                        if len(results) >= limit:
                            return results
        return results

    async def get_neighbor_albums(self, limit: int = 12) -> list[dict]:
        """Get albums from Last.fm neighbors (users with similar taste)."""
        try:
            r = await self._client.get(self.BASE_URL, params={
                "method": "user.getNeighbours",
                "user": settings.lastfm_username,
                "api_key": settings.lastfm_api_key,
                "format": "json",
                "limit": 8,
            })
            neighbors = r.json().get("neighbours", {}).get("user", [])
            neighbor_names = [n["name"] for n in neighbors[:6]]
        except Exception:
            return []

        async def _neighbor_top_albums(username: str) -> list[dict]:
            try:
                r = await self._client.get(self.BASE_URL, params={
                    "method": "user.getTopAlbums",
                    "user": username,
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                    "period": "1month",
                    "limit": 5,
                })
                albums = r.json().get("topalbums", {}).get("album", [])
                return [{
                    "artist": a.get("artist", {}).get("name", ""),
                    "album": a.get("name", ""),
                    "mb_id": a.get("mbid", ""),
                    "cover_url": next(
                        (img["#text"] for img in a.get("image", [])
                         if img["size"] == "extralarge" and img["#text"]
                         and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]),
                        None,
                    ),
                    "neighbor": username,
                } for a in albums if a.get("artist", {}).get("name") and a.get("name")]
            except Exception:
                return []

        neighbor_results = await asyncio.gather(*[_neighbor_top_albums(n) for n in neighbor_names])

        # Interleave and deduplicate
        seen = set()
        results = []
        for i in range(5):
            for neighbor_list in neighbor_results:
                if i < len(neighbor_list):
                    item = neighbor_list[i]
                    key = f"{item['artist'].lower()}|{item['album'].lower()}"
                    if key not in seen:
                        seen.add(key)
                        results.append(item)
                        if len(results) >= limit:
                            return results
        return results


lastfm_client = LastFmClient()
