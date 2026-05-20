import asyncio
import re
import html
import httpx
import urllib.parse

from app.services.redacted import red_client

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    return _client


def _first_meta(text: str, property_name: str) -> str:
    pattern = rf'<meta\s+(?:property|name)="{re.escape(property_name)}"\s+content="([^"]+)"'
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _clean_apple_artist_title(value: str) -> str:
    return re.sub(r"\s+on Apple Music$", "", value or "", flags=re.IGNORECASE).strip()


def _artist_result(name: str, image: str | None = None, mb_id: str = "", source: str = "") -> dict | None:
    name = (name or "").strip()
    if not name:
        return None
    return {
        "kind": "artist",
        "artist": name,
        "image": image or None,
        "mb_id": mb_id,
        "source": source,
    }


def _strip_red_markup(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[(?:/?(?:b|i|u|artist|url|align|size|quote|hide|spoiler|img|code)[^\]]*)\]", " ", text, flags=re.IGNORECASE)
    text = text.replace("&ndash;", "-")
    return text


def _red_summary(value: str) -> str:
    text = _strip_red_markup(value)
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -*\t\r\n")
        if not line:
            continue
        if re.match(r"^\d{1,3}\.\s+", line):
            continue
        if re.search(r"\(\d{1,2}:\d{2}\)", line) and len(line) < 140:
            continue
        if line.lower() in {"track list", "tracklist"}:
            continue
        lines.append(line)
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 900:
        text = text[:900].rsplit(" ", 1)[0].rstrip(".,;:") + "..."
    return text


def _red_artist_names(music_info: dict) -> list[str]:
    names = []
    for key in ("artists", "with", "composers", "dj", "conductor", "remixedBy", "producer"):
        for item in music_info.get(key) or []:
            name = (item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _red_group_result(group_id: str, data: dict) -> dict | None:
    group = data.get("group") or {}
    if not group:
        return None
    names = _red_artist_names(group.get("musicInfo") or {})
    artist = " / ".join(names) or "Various Artists"
    album = group.get("name") or ""
    if not album:
        return None
    body = group.get("bbBody") or group.get("wikiBody") or ""
    return {
        "artist": artist,
        "album": album,
        "cover_url": group.get("wikiImage") or None,
        "mb_id": "",
        "year": str(group.get("year") or ""),
        "source": "red",
        "red_group_id": group_id,
        "red_summary": _red_summary(body),
        "red_tags": group.get("tags") or [],
    }


async def resolve_url(url: str) -> dict | None:
    """Resolve a music platform URL to album or artist info."""
    url = url.strip()

    # RED torrent group: https://redacted.sh/torrents.php?id=2786495
    m = re.match(r'https?://(?:www\.)?redacted\.sh/torrents\.php\?(?:[^#]*&)?id=(\d+)', url)
    if m:
        group_id = m.group(1)
        try:
            return _red_group_result(group_id, await red_client.get_torrent_group(group_id))
        except Exception:
            return None

    # RED artist: https://redacted.sh/artist.php?id=10530 or ?artistname=F.S.Blumm
    m = re.match(r'https?://(?:www\.)?redacted\.sh/artist\.php\?([^#]+)', url)
    if m:
        query = urllib.parse.parse_qs(m.group(1))
        try:
            info = await red_client.get_artist_info(
                artist_id=(query.get("id") or [""])[0],
                artist_name=(query.get("artistname") or [""])[0],
            )
            return _artist_result(
                info.get("name", ""),
                info.get("image") or None,
                source="red",
            )
        except Exception:
            return None

    # Last.fm: https://www.last.fm/music/Artist/Album
    m = re.match(r'https?://(?:www\.)?last\.fm/music/([^/]+)/([^/?#]+)', url)
    if m:
        artist = urllib.parse.unquote_plus(m.group(1)).replace('+', ' ')
        album = urllib.parse.unquote_plus(m.group(2)).replace('+', ' ')
        return {"artist": artist, "album": album, "cover_url": None, "mb_id": ""}

    # Last.fm artist: https://www.last.fm/music/Artist
    m = re.match(r'https?://(?:www\.)?last\.fm/music/([^/?#]+)(?:[/?#]|$)', url)
    if m:
        artist = urllib.parse.unquote_plus(m.group(1)).replace('+', ' ')
        return _artist_result(artist, source="lastfm")

    # Deezer: https://www.deezer.com/*/album/12345
    m = re.match(r'https?://(?:www\.)?deezer\.com/(?:[a-z]+/)?album/(\d+)', url)
    if m:
        try:
            r = await _get_client().get(f"https://api.deezer.com/album/{m.group(1)}")
            data = r.json()
            artist = data.get("artist", {}).get("name", "")
            album = data.get("title", "")
            cover = data.get("cover_xl") or data.get("cover_big") or data.get("cover", "")
            return {"artist": artist, "album": album, "cover_url": cover, "mb_id": "", "deezer_id": m.group(1)}
        except Exception:
            pass

    # Deezer artist: https://www.deezer.com/*/artist/12345
    m = re.match(r'https?://(?:www\.)?deezer\.com/(?:[a-z]+/)?artist/(\d+)', url)
    if m:
        try:
            r = await _get_client().get(f"https://api.deezer.com/artist/{m.group(1)}")
            data = r.json()
            if not data.get("error"):
                return _artist_result(
                    data.get("name", ""),
                    data.get("picture_xl") or data.get("picture_big") or data.get("picture"),
                    source="deezer",
                )
        except Exception:
            pass

    # Apple Music / iTunes: https://music.apple.com/*/album/*/{id}
    m = re.match(r'https?://music\.apple\.com/[^/]+/album/[^/]+/(\d+)', url)
    if m:
        try:
            r = await _get_client().get(f"https://itunes.apple.com/lookup?id={m.group(1)}")
            results = r.json().get("results", [])
            for item in results:
                if item.get("wrapperType") == "collection":
                    artist = item.get("artistName", "")
                    album = item.get("collectionName", "")
                    cover = item.get("artworkUrl100", "").replace("100x100", "600x600")
                    return {"artist": artist, "album": album, "cover_url": cover, "mb_id": ""}
        except Exception:
            pass

    # Apple Music artist: https://music.apple.com/*/artist/*/{id}
    m = re.match(r'https?://music\.apple\.com/(?:[a-z]{2}/)?artist/[^/]+/(\d+)', url)
    if m:
        try:
            r = await _get_client().get(f"https://itunes.apple.com/lookup?id={m.group(1)}")
            for item in r.json().get("results", []):
                if item.get("wrapperType") == "artist":
                    return _artist_result(item.get("artistName", ""), source="apple")
        except Exception:
            pass
        try:
            r = await _get_client().get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            title = _clean_apple_artist_title(_first_meta(r.text, "og:title"))
            image = _first_meta(r.text, "og:image")
            return _artist_result(title, image, source="apple")
        except Exception:
            pass

    # Discogs release: https://www.discogs.com/release/123456 or .../master/123456
    m = re.match(r'https?://(?:www\.)?discogs\.com/(?:[^/]+/)?(release|master)/(\d+)', url)
    if m:
        kind, disc_id = m.group(1), m.group(2)
        try:
            r = await _get_client().get(
                f"https://api.discogs.com/{kind}s/{disc_id}",
                headers={"User-Agent": "Redwave/1.0"},
            )
            data = r.json()
            if kind == "master":
                artist = (data.get("artists") or [{}])[0].get("name", "").rstrip(" 0123456789").strip()
                album = data.get("title", "")
                cover = data.get("images", [{}])[0].get("uri", "") if data.get("images") else ""
            else:
                artist = (data.get("artists") or [{}])[0].get("name", "").rstrip(" 0123456789").strip()
                album = data.get("title", "")
                cover = data.get("images", [{}])[0].get("uri", "") if data.get("images") else ""
            if artist and album:
                return {"artist": artist, "album": album, "cover_url": cover or None, "mb_id": ""}
        except Exception:
            pass

    # Discogs artist: https://www.discogs.com/artist/12345-Name
    m = re.match(r'https?://(?:www\.)?discogs\.com/artist/(\d+)', url)
    if m:
        try:
            r = await _get_client().get(
                f"https://api.discogs.com/artists/{m.group(1)}",
                headers={"User-Agent": "Redwave/1.0"},
            )
            data = r.json()
            images = data.get("images") or []
            image = images[0].get("uri", "") if images else ""
            return _artist_result(data.get("name", ""), image, source="discogs")
        except Exception:
            pass

    # MusicBrainz release: https://musicbrainz.org/release/{mbid}
    m = re.match(r'https?://(?:www\.)?musicbrainz\.org/release/([0-9a-f-]{36})(?:[/?#]|$)', url)
    if m:
        mb_id = m.group(1)
        try:
            r = await _get_client().get(
                f"https://musicbrainz.org/ws/2/release/{mb_id}",
                params={"fmt": "json", "inc": "artist-credits+release-groups"},
                headers={"User-Agent": "Redwave/1.0 (redwave@example.com)"},
            )
            data = r.json()
            artist = (data.get("artist-credit") or [{}])[0].get("artist", {}).get("name", "")
            album = data.get("title", "")
            if artist and album:
                return {"artist": artist, "album": album, "cover_url": None, "mb_id": mb_id}
        except Exception:
            pass

    # MusicBrainz release-group: https://musicbrainz.org/release-group/{mbid}
    m = re.match(r'https?://(?:www\.)?musicbrainz\.org/release-group/([0-9a-f-]{36})(?:[/?#]|$)', url)
    if m:
        mb_id = m.group(1)
        try:
            r = await _get_client().get(
                f"https://musicbrainz.org/ws/2/release-group/{mb_id}",
                params={"fmt": "json", "inc": "artist-credits"},
                headers={"User-Agent": "Redwave/1.0 (redwave@example.com)"},
            )
            data = r.json()
            artist = (data.get("artist-credit") or [{}])[0].get("artist", {}).get("name", "")
            album = data.get("title", "")
            if artist and album:
                return {"artist": artist, "album": album, "cover_url": None, "mb_id": ""}
        except Exception:
            pass

    # MusicBrainz artist: https://musicbrainz.org/artist/{mbid}
    m = re.match(r'https?://(?:www\.)?musicbrainz\.org/artist/([0-9a-f-]{36})(?:[/?#]|$)', url)
    if m:
        mb_id = m.group(1)
        try:
            r = await _get_client().get(
                f"https://musicbrainz.org/ws/2/artist/{mb_id}",
                params={"fmt": "json"},
                headers={"User-Agent": "Redwave/1.0 (redwave@example.com)"},
            )
            data = r.json()
            return _artist_result(data.get("name", ""), mb_id=mb_id, source="musicbrainz")
        except Exception:
            pass

    # Spotify: https://open.spotify.com/album/{id}
    # Use Spotify's public page JSON-LD + oEmbed (no auth needed)
    m = re.match(r'https?://open\.spotify\.com/album/([A-Za-z0-9]+)', url)
    if m:
        album_id = m.group(1)
        try:
            import json as _json
            client = _get_client()
            # Fetch both in parallel
            page_r, oembed_r = await asyncio.gather(
                client.get(
                    f"https://open.spotify.com/album/{album_id}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                ),
                client.get(
                    "https://open.spotify.com/oembed",
                    params={"url": f"https://open.spotify.com/album/{album_id}"},
                ),
            )
            # oEmbed → album name + high-res cover
            oembed = oembed_r.json()
            album_name = oembed.get("title", "")
            cover = oembed.get("thumbnail_url", "")

            # JSON-LD on main page → artist name
            ld_m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page_r.text, re.DOTALL)
            artist_name = ""
            if ld_m:
                ld = _json.loads(ld_m.group(1))
                # byArtist field
                by_artist = ld.get("byArtist")
                if isinstance(by_artist, dict):
                    artist_name = by_artist.get("name", "")
                elif isinstance(by_artist, list) and by_artist:
                    artist_name = by_artist[0].get("name", "")
                # Fallback: parse description "Listen to X on Spotify · album · Artist · year · N songs"
                if not artist_name:
                    desc = ld.get("description", "")
                    parts = [p.strip() for p in desc.split("·")]
                    # parts: ["Listen to X on Spotify ", "album ", "Artist ", "year ", "N songs"]
                    if len(parts) >= 3:
                        artist_name = parts[2].strip()

            if album_name and artist_name:
                return {"artist": artist_name, "album": album_name, "cover_url": cover or None, "mb_id": ""}
        except Exception:
            pass
        return None

    # Spotify artist: https://open.spotify.com/artist/{id}
    m = re.match(r'https?://open\.spotify\.com/artist/([A-Za-z0-9]+)', url)
    if m:
        artist_id = m.group(1)
        try:
            import json as _json
            client = _get_client()
            page_r, oembed_r = await asyncio.gather(
                client.get(
                    f"https://open.spotify.com/artist/{artist_id}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                ),
                client.get("https://open.spotify.com/oembed", params={"url": url}),
            )
            name = ""
            ld_m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page_r.text, re.DOTALL)
            if ld_m:
                ld = _json.loads(ld_m.group(1))
                name = ld.get("name", "")
            oembed = oembed_r.json()
            return _artist_result(
                name or oembed.get("title", ""),
                oembed.get("thumbnail_url", ""),
                source="spotify",
            )
        except Exception:
            pass
        return None

    # Bandcamp: https://{artist}.bandcamp.com/album/{slug}
    m = re.match(r'https?://[^/]+\.bandcamp\.com/album/[^/?#]+', url)
    if m:
        try:
            import json as _json
            r = await _get_client().get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            text = r.text
            # Try JSON-LD first
            ld_m = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
            if ld_m:
                ld = _json.loads(ld_m.group(1))
                album_name = ld.get("name", "")
                artist_name = ""
                by_artist = ld.get("byArtist")
                if isinstance(by_artist, dict):
                    artist_name = by_artist.get("name", "")
                elif isinstance(by_artist, list) and by_artist:
                    artist_name = by_artist[0].get("name", "")
                cover = ""
                image = ld.get("image")
                if isinstance(image, str):
                    cover = image
                elif isinstance(image, list) and image:
                    cover = image[0]
                if album_name and artist_name:
                    return {"artist": artist_name, "album": album_name, "cover_url": cover or None, "mb_id": ""}
            # Fallback: og meta tags
            og_title = re.search(r'<meta property="og:title"\s+content="([^"]+)"', text)
            og_image = re.search(r'<meta property="og:image"\s+content="([^"]+)"', text)
            og_site = re.search(r'<meta property="og:site_name"\s+content="([^"]+)"', text)
            if og_title and og_site:
                return {
                    "artist": og_site.group(1),
                    "album": og_title.group(1).split(" | ")[0].strip(),
                    "cover_url": og_image.group(1) if og_image else None,
                    "mb_id": "",
                }
        except Exception:
            pass

    # Bandcamp artist root: https://artist.bandcamp.com/
    m = re.match(r'https?://[^/]+\.bandcamp\.com/?(?:[?#].*)?$', url)
    if m:
        try:
            r = await _get_client().get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            title = _first_meta(r.text, "og:site_name") or _first_meta(r.text, "og:title")
            image = _first_meta(r.text, "og:image")
            return _artist_result(title, image, source="bandcamp")
        except Exception:
            pass

    return None


def is_url(q: str) -> bool:
    return q.startswith("http://") or q.startswith("https://")
