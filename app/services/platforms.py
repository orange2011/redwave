import asyncio
import httpx
import urllib.parse


_client = None

def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    return _client


async def _deezer_link(artist: str, album: str) -> str:
    try:
        q = urllib.parse.quote(f"{artist} {album}")
        r = await _get_client().get(f"https://api.deezer.com/search/album?q={q}&limit=5")
        data = r.json()
        for item in data.get("data", []):
            return item.get("link", "")
    except Exception:
        pass
    q = urllib.parse.quote(f"{artist} {album}")
    return f"https://www.deezer.com/search/{q}"


async def _spotify_link(artist: str, album: str, client_id: str = "", client_secret: str = "") -> str:
    q = urllib.parse.quote(f"{artist} {album}")
    if not client_id or not client_secret:
        return f"https://open.spotify.com/search/{urllib.parse.quote(artist + ' ' + album)}"
    try:
        import base64
        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        tok = await _get_client().post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {creds}"},
            data={"grant_type": "client_credentials"},
        )
        token = tok.json().get("access_token", "")
        if not token:
            raise Exception("no token")
        r = await _get_client().get(
            "https://api.spotify.com/v1/search",
            params={"q": f"album:{album} artist:{artist}", "type": "album", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        items = r.json().get("albums", {}).get("items", [])
        if items:
            return items[0].get("external_urls", {}).get("spotify", "")
    except Exception:
        pass
    return f"https://open.spotify.com/search/{urllib.parse.quote(artist + ' ' + album)}"


async def _qobuz_link(artist: str, album: str, app_id: str = "", token: str = "") -> str:
    if app_id and token:
        try:
            r = await _get_client().get(
                "https://www.qobuz.com/api.json/0.2/album/search",
                params={"query": f"{artist} {album}", "limit": 5},
                headers={"X-App-Id": app_id, "X-User-Auth-Token": token},
            )
            items = r.json().get("albums", {}).get("items", [])
            if items:
                album_id = items[0].get("id", "")
                if album_id:
                    return f"https://play.qobuz.com/album/{album_id}"
        except Exception:
            pass
    q = urllib.parse.quote(f"{artist} {album}")
    return f"https://www.qobuz.com/us-en/search/albums/{q}"


async def _discogs_link(artist: str, album: str) -> str:
    try:
        r = await _get_client().get(
            "https://api.discogs.com/database/search",
            params={"artist": artist, "release_title": album, "type": "master", "per_page": 1},
            headers={"User-Agent": "Redwave/1.0"},
        )
        results = r.json().get("results", [])
        if results:
            return f"https://www.discogs.com{results[0]['uri']}"
    except Exception:
        pass
    q = urllib.parse.quote(f"{artist} {album}")
    return f"https://www.discogs.com/search/?q={q}&type=master"


async def get_platform_links(artist: str, album: str, itunes_url: str = "",
                              mb_id: str = "",
                              spotify_id: str = "", spotify_secret: str = "",
                              qobuz_app_id: str = "", qobuz_token: str = "") -> list[dict]:
    deezer, spotify, qobuz, discogs = await asyncio.gather(
        _deezer_link(artist, album),
        _spotify_link(artist, album, spotify_id, spotify_secret),
        _qobuz_link(artist, album, qobuz_app_id, qobuz_token),
        _discogs_link(artist, album),
    )
    apple = itunes_url or f"https://music.apple.com/search?term={urllib.parse.quote(artist + ' ' + album)}"

    q = urllib.parse.quote(f"{artist} {album}")
    lastfm = f"https://www.last.fm/music/{urllib.parse.quote(artist)}/{urllib.parse.quote(album)}"
    mb = f"https://musicbrainz.org/release/{mb_id}" if mb_id else f"https://musicbrainz.org/search?query={q}&type=release"

    return [
        {"name": "Spotify",       "url": spotify,  "color": "#1DB954"},
        {"name": "Apple Music",   "url": apple,    "color": "#FC3C44"},
        {"name": "Deezer",        "url": deezer,   "color": "#A238FF"},
        {"name": "Qobuz",         "url": qobuz,    "color": "#0070E0"},
        {"name": "Discogs",       "url": discogs,  "color": "#F5A623"},
        {"name": "Last.fm",       "url": lastfm,   "color": "#D51007"},
        {"name": "MusicBrainz",   "url": mb,       "color": "#BA478F"},
    ]
