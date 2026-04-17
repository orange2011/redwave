import asyncio
import re
import httpx
import urllib.parse

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    return _client


async def resolve_url(url: str) -> dict | None:
    """Try to resolve a platform URL to album info. Returns dict with artist/album/cover_url/mb_id or None."""
    url = url.strip()

    # Last.fm: https://www.last.fm/music/Artist/Album
    m = re.match(r'https?://(?:www\.)?last\.fm/music/([^/]+)/([^/?#]+)', url)
    if m:
        artist = urllib.parse.unquote_plus(m.group(1)).replace('+', ' ')
        album = urllib.parse.unquote_plus(m.group(2)).replace('+', ' ')
        return {"artist": artist, "album": album, "cover_url": None, "mb_id": ""}

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

    return None


def is_url(q: str) -> bool:
    return q.startswith("http://") or q.startswith("https://")
