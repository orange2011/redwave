import unittest
from pathlib import Path

from app.services.search_service import (
    album_score,
    artist_score,
    compact_text,
    equivalent_text,
    library_query_variants,
    match_text,
    track_score,
)


class SearchRankingTests(unittest.TestCase):
    def test_ampersand_and_punctuation_normalize(self):
        self.assertEqual(match_text("Poems, Prayers & Promises"), "poems prayers and promises")
        self.assertTrue(equivalent_text("Poems Prayers and Promises", "Poems, Prayers & Promises"))
        self.assertEqual(compact_text("Poems, Prayers & Promises"), "poems prayers promises")
        self.assertEqual(compact_text("The Divine Feminine"), "divine feminine")
        self.assertEqual(match_text("Út"), "ut")

    def test_exact_library_album_beats_external_album(self):
        query = "The Divine Feminine"
        library_album = {
            "artist": "Mac Miller",
            "album": "The Divine Feminine",
            "cover_url": "/api/navidrome/cover/abc",
            "in_collection": True,
            "source": "navidrome",
            "song_count": 10,
        }
        external_album = {
            "artist": "Mac Miller",
            "album": "The Divine Feminine",
            "cover_url": "https://example.invalid/cover.jpg",
        }
        self.assertGreater(album_score(query, library_album), album_score(query, external_album))

    def test_exact_library_song_beats_external_song(self):
        query = "I Love My Computer"
        library_track = {
            "artist": "Ninajirachi",
            "track": "I Love My Computer",
            "album": "I Love My Computer",
            "cover_url": "/api/navidrome/cover/abc",
            "in_collection": True,
            "source": "navidrome",
        }
        external_track = {
            "artist": "Bad Religion",
            "track": "I Love My Computer",
            "album": "The New America",
            "cover_url": "https://example.invalid/cover.jpg",
        }
        self.assertGreater(track_score(query, library_track), track_score(query, external_track))

    def test_self_titled_album_does_not_beat_exact_artist(self):
        query = "John Denver"
        artist = {"name": "John Denver", "listeners": 1200000, "image": "https://example.invalid/a.jpg"}
        self_titled_album = {
            "artist": "John Denver",
            "album": "John Denver",
            "cover_url": "https://example.invalid/c.jpg",
        }
        self.assertGreater(artist_score(query, artist), album_score(query, self_titled_album))

    def test_exact_album_beats_low_listener_same_name_artist(self):
        query = "The Divine Feminine"
        album = {
            "artist": "Mac Miller",
            "album": "The Divine Feminine",
            "cover_url": "/api/navidrome/cover/divine",
            "in_collection": True,
            "source": "navidrome",
        }
        artist = {"name": "The Divine Feminine", "listeners": 11, "image": ""}
        self.assertGreater(album_score(query, album), artist_score(query, artist))

    def test_library_query_variants_handle_long_bulleted_artist_credit(self):
        variants = library_query_variants(
            "Slow Village • Nash • FNT • Rebelo • Hívatlanok • NB • Goulasch • 4Tress Posse2"
        )

        self.assertIn("Posse2", variants)
        self.assertIn("4Tress Posse2", variants)

    def test_search_template_shows_busy_state(self):
        template = Path("app/templates/search.html").read_text(encoding="utf-8")

        self.assertIn("search-busy-icon", template)
        self.assertNotIn("search-loading-spinner", template)
        self.assertIn("event.preventDefault()", template)
        self.assertIn("aria-busy", template)


if __name__ == "__main__":
    unittest.main()
