import json
import unittest

from app.services import url_import


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    async def get(self, url, params=None, headers=None):
        if "api.deezer.com/album/123" in url:
            return FakeResponse(
                {
                    "artist": {"name": "John Denver"},
                    "title": "Poems, Prayers & Promises",
                    "cover_xl": "https://img.example/deezer-album.jpg",
                }
            )
        if "api.deezer.com/artist/456" in url:
            return FakeResponse({"name": "Daft Punk", "picture_xl": "https://img.example/daft.jpg"})
        if "itunes.apple.com/lookup" in url and ("id=111" in url or (params and params.get("id") == "111")):
            return FakeResponse(
                {
                    "results": [
                        {
                            "wrapperType": "collection",
                            "artistName": "Mac Miller",
                            "collectionName": "The Divine Feminine",
                            "artworkUrl100": "https://img.example/100x100bb.jpg",
                        }
                    ]
                }
            )
        if "itunes.apple.com/lookup" in url and ("id=222" in url or (params and params.get("id") == "222")):
            return FakeResponse({"results": [{"wrapperType": "artist", "artistName": "Mac Miller"}]})
        if "musicbrainz.org/ws/2/release/" in url:
            return FakeResponse(
                {"artist-credit": [{"artist": {"name": "Ninajirachi"}}], "title": "I Love My Computer"}
            )
        if "musicbrainz.org/ws/2/release-group/" in url:
            return FakeResponse(
                {"artist-credit": [{"artist": {"name": "John Denver"}}], "title": "Poems, Prayers & Promises"}
            )
        if "musicbrainz.org/ws/2/artist/" in url:
            return FakeResponse({"name": "Matt Maltese"})
        if "api.discogs.com/releases/333" in url or "api.discogs.com/masters/444" in url:
            return FakeResponse(
                {
                    "artists": [{"name": "John Denver"}],
                    "title": "Poems, Prayers & Promises",
                    "images": [{"uri": "https://img.example/discogs.jpg"}],
                }
            )
        if "api.discogs.com/artists/555" in url:
            return FakeResponse({"name": "John Denver", "images": [{"uri": "https://img.example/artist.jpg"}]})
        if "open.spotify.com/album/" in url:
            return FakeResponse(
                text='<script type="application/ld+json">'
                + json.dumps(
                    {
                        "byArtist": {"name": "John Denver"},
                        "description": "Listen to Poems, Prayers & Promises on Spotify",
                    }
                )
                + "</script>"
            )
        if "open.spotify.com/artist/" in url:
            return FakeResponse(text='<script type="application/ld+json">{"name":"George Thorogood & The Destroyers"}</script>')
        if "open.spotify.com/oembed" in url and params and "album" in params.get("url", ""):
            return FakeResponse(
                {"title": "Poems, Prayers & Promises", "thumbnail_url": "https://img.example/spotify-album.jpg"}
            )
        if "open.spotify.com/oembed" in url and params and "artist" in params.get("url", ""):
            return FakeResponse(
                {"title": "George Thorogood & The Destroyers", "thumbnail_url": "https://img.example/spotify-artist.jpg"}
            )
        if "artist.bandcamp.com/album/" in url:
            return FakeResponse(
                text='<script type="application/ld+json">'
                + json.dumps(
                    {
                        "name": "Bandcamp Album",
                        "byArtist": {"name": "Bandcamp Artist"},
                        "image": "https://img.example/bandcamp.jpg",
                    }
                )
                + "</script>"
            )
        if "artist.bandcamp.com" in url:
            return FakeResponse(
                text='<meta property="og:site_name" content="Bandcamp Artist"><meta property="og:image" content="https://img.example/bandcamp-artist.jpg">'
            )
        return FakeResponse({})


class FakeRedClient:
    async def get_torrent_group(self, group_id):
        return {
            "group": {
                "name": "Wellen Formen",
                "year": 2026,
                "wikiImage": "https://img.example/red.jpg",
                "bbBody": "[b][artist]F.S.Blumm[/artist] - WELLEN FORMEN[/b]\n"
                "[b]01.[/b] Reinfeld Trainride [i](03:27)[/i]\n"
                "A warm miniature album note from RED.",
                "musicInfo": {"artists": [{"id": 10530, "name": "F.S. Blumm"}]},
                "tags": ["ambient", "electronic"],
            },
            "torrents": [],
        }

    async def get_artist_info(self, artist_id=None, artist_name=""):
        return {
            "id": artist_id or 10530,
            "name": artist_name or "F.S. Blumm",
            "image": "https://img.example/red-artist.jpg",
        }


class UrlImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_client = url_import._client
        self._old_tracker_client_for = url_import.tracker_client_for
        url_import._client = FakeClient()
        self.fake_tracker = FakeRedClient()
        url_import.tracker_client_for = lambda tracker: self.fake_tracker

    async def asyncTearDown(self):
        url_import._client = self._old_client
        url_import.tracker_client_for = self._old_tracker_client_for

    async def test_lastfm_album_and_artist_urls(self):
        album = await url_import.resolve_url("https://www.last.fm/music/John+Denver/Poems,+Prayers+&+Promises")
        artist = await url_import.resolve_url("https://www.last.fm/music/Matt+Maltese")
        self.assertEqual(album["artist"], "John Denver")
        self.assertEqual(album["album"], "Poems, Prayers & Promises")
        self.assertEqual(artist["kind"], "artist")
        self.assertEqual(artist["artist"], "Matt Maltese")

    async def test_deezer_urls(self):
        album = await url_import.resolve_url("https://www.deezer.com/us/album/123")
        artist = await url_import.resolve_url("https://www.deezer.com/us/artist/456")
        self.assertEqual(album["album"], "Poems, Prayers & Promises")
        self.assertEqual(artist["artist"], "Daft Punk")

    async def test_apple_urls(self):
        album = await url_import.resolve_url("https://music.apple.com/us/album/the-divine-feminine/111")
        artist = await url_import.resolve_url("https://music.apple.com/us/artist/mac-miller/222")
        self.assertEqual(album["artist"], "Mac Miller")
        self.assertEqual(album["album"], "The Divine Feminine")
        self.assertEqual(artist["artist"], "Mac Miller")

    async def test_musicbrainz_urls(self):
        release = await url_import.resolve_url("https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111")
        group = await url_import.resolve_url("https://musicbrainz.org/release-group/22222222-2222-2222-2222-222222222222")
        artist = await url_import.resolve_url("https://musicbrainz.org/artist/33333333-3333-3333-3333-333333333333")
        self.assertEqual(release["album"], "I Love My Computer")
        self.assertEqual(group["artist"], "John Denver")
        self.assertEqual(artist["kind"], "artist")
        self.assertEqual(artist["artist"], "Matt Maltese")

    async def test_discogs_urls(self):
        release = await url_import.resolve_url("https://www.discogs.com/release/333-title")
        master = await url_import.resolve_url("https://www.discogs.com/master/444-title")
        artist = await url_import.resolve_url("https://www.discogs.com/artist/555-John-Denver")
        self.assertEqual(release["album"], "Poems, Prayers & Promises")
        self.assertEqual(master["artist"], "John Denver")
        self.assertEqual(artist["artist"], "John Denver")

    async def test_spotify_urls(self):
        album = await url_import.resolve_url("https://open.spotify.com/album/abc123?si=test")
        artist = await url_import.resolve_url("https://open.spotify.com/artist/4n31svBA9GGIYxGxgrQaRK?si=test")
        self.assertEqual(album["artist"], "John Denver")
        self.assertEqual(album["album"], "Poems, Prayers & Promises")
        self.assertEqual(artist["kind"], "artist")
        self.assertEqual(artist["artist"], "George Thorogood & The Destroyers")

    async def test_bandcamp_urls(self):
        album = await url_import.resolve_url("https://artist.bandcamp.com/album/example")
        artist = await url_import.resolve_url("https://artist.bandcamp.com/")
        self.assertEqual(album["artist"], "Bandcamp Artist")
        self.assertEqual(album["album"], "Bandcamp Album")
        self.assertEqual(artist["kind"], "artist")
        self.assertEqual(artist["artist"], "Bandcamp Artist")

    async def test_red_urls(self):
        album = await url_import.resolve_url("https://redacted.sh/torrents.php?id=2786495")
        artist = await url_import.resolve_url("https://redacted.sh/artist.php?id=10530")
        self.assertEqual(album["source"], "red")
        self.assertEqual(album["artist"], "F.S. Blumm")
        self.assertEqual(album["album"], "Wellen Formen")
        self.assertEqual(album["year"], "2026")
        self.assertIn("album note", album["red_summary"])
        self.assertEqual(artist["kind"], "artist")
        self.assertEqual(artist["artist"], "F.S. Blumm")

    async def test_ops_urls(self):
        album = await url_import.resolve_url("https://orpheus.network/torrents.php?id=2786495")
        artist = await url_import.resolve_url("https://orpheus.network/artist.php?id=10530")
        self.assertEqual(album["source"], "ops")
        self.assertEqual(album["album"], "Wellen Formen")
        self.assertEqual(artist["source"], "ops")


if __name__ == "__main__":
    unittest.main()
