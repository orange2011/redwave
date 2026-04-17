import asyncio
import urllib.parse
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.templates_config import templates
from app.services.lastfm import lastfm_client, get_artist_image, get_mb_discography
from app.services.navidrome import get_collection
from app.utils import normalize_album, normalize_artist

router = APIRouter()


@router.get("/artist/{name}", response_class=HTMLResponse)
async def artist_page(request: Request, name: str):
    artist_name = urllib.parse.unquote_plus(name)

    info, collection = await asyncio.gather(
        lastfm_client.get_artist_info(artist_name),
        get_collection(),
    )

    # Artist image fallback
    if info and not info.get("image"):
        image = await get_artist_image(artist_name)
        if not image:
            image = None
        info["image"] = image

    mb_id = (info or {}).get("mb_id", "")
    discography = await get_mb_discography(mb_id, artist_name)

    # Build collection lookup for badges + cover overrides
    import unicodedata
    def _nfc(s): return unicodedata.normalize("NFC", s)
    collection_map = {
        f"{normalize_artist(_nfc(a['artist']))}|{normalize_album(a['album']).lower()}": a
        for a in collection
    }

    # Enrich discography entries with collection info
    for label, releases in discography.items():
        for r in releases:
            key = f"{normalize_artist(artist_name)}|{normalize_album(r['album']).lower()}"
            if key in collection_map:
                r["in_collection"] = True
                r["nav_cover"] = collection_map[key].get("cover_url", "")
            else:
                r["in_collection"] = False
                r["nav_cover"] = ""

    q = urllib.parse.quote(artist_name)
    external_links = [
        {"name": "Last.fm",      "url": (info or {}).get("url") or f"https://www.last.fm/music/{q}",                                           "color": "#D51007"},
        {"name": "MusicBrainz",  "url": f"https://musicbrainz.org/artist/{mb_id}" if mb_id else f"https://musicbrainz.org/search?query={q}&type=artist", "color": "#BA478F"},
        {"name": "ListenBrainz", "url": f"https://listenbrainz.org/artist/{mb_id}" if mb_id else f"https://listenbrainz.org/search/?search_term={q}",     "color": "#353070"},
        {"name": "Spotify",      "url": f"https://open.spotify.com/search/{q}",                                                                "color": "#1DB954"},
        {"name": "Qobuz",        "url": f"https://www.qobuz.com/us-en/search/artists/{q}",                                                     "color": "#0070E0"},
        {"name": "Discogs",      "url": f"https://www.discogs.com/search/?q={q}&type=artist",                                                   "color": "#F5A623"},
        {"name": "YouTube",      "url": f"https://www.youtube.com/results?search_query={q}",                                                    "color": "#FF0000"},
    ]

    return templates.TemplateResponse("artist.html", {
        "request": request,
        "artist": (info or {}).get("name", artist_name),
        "info": info or {},
        "discography": discography,
        "external_links": external_links,
    })
