import unittest
from pathlib import Path

from app.services.discovery import (
    artist_gap_targets,
    coerce_genre,
    collection_key,
    collection_keys,
    mark_collection,
    missing_albums_for_artist,
)
from app.utils import build_collection_lookup, find_collection_album, normalize_artist

ROOT = Path(__file__).resolve().parents[1]


class DiscoveryTests(unittest.TestCase):
    def test_genre_aliases_are_normalized(self):
        self.assertEqual(coerce_genre("hip hop"), "hip-hop")
        self.assertEqual(coerce_genre("R&B"), "rnb")
        self.assertEqual(coerce_genre("BlackMetal"), "black metal")

    def test_collection_keys_match_edition_variants(self):
        owned = collection_keys([
            {"artist": "John Denver", "album": "Poems, Prayers & Promises (Remastered)"},
            {"artist": "Ninajirachi", "album": "girl EDM"},
        ])

        self.assertIn(collection_key("John Denver", "Poems, Prayers & Promises"), owned)
        self.assertIn(collection_key("Ninajirachi", "girl EDM (disc 1)"), owned)

    def test_mark_collection_flags_owned_albums(self):
        owned = {collection_key("Ninajirachi", "I Love My Computer")}
        albums = mark_collection([
            {"artist": "Ninajirachi", "album": "I Love My Computer"},
            {"artist": "Ninajirachi", "album": "Girl EDM"},
        ], owned)

        self.assertTrue(albums[0]["in_collection"])
        self.assertFalse(albums[1]["in_collection"])

    def test_collection_match_handles_artist_script_mismatch_for_specific_album(self):
        collection = [
            {
                "artist": "菊池ひみこ",
                "album": "Flying Beagle",
                "year": "1987",
                "cover_url": "/api/navidrome/cover/flying",
                "mb_id": "local-release",
            }
        ]
        lookup = build_collection_lookup(collection)

        match = find_collection_album("Himiko Kikuchi", "Flying Beagle", lookup=lookup)

        self.assertIsNotNone(match)
        self.assertEqual(match["artist"], "菊池ひみこ")

    def test_artist_normalization_keeps_non_latin_names(self):
        self.assertEqual(normalize_artist("菊池ひみこ"), "菊池ひみこ")

    def test_collection_match_rejects_generic_single_title_artist_mismatch(self):
        collection = [
            {"artist": "Weezer", "album": "Weezer", "year": "2019"},
        ]
        lookup = build_collection_lookup(collection)

        self.assertIsNone(find_collection_album("Another Artist", "Weezer", lookup=lookup))
        self.assertIsNotNone(find_collection_album("Another Artist", "Weezer", lookup=lookup, year="2019"))

    def test_collection_match_uses_year_for_same_artist_same_title_albums(self):
        collection = [
            {"artist": "Weezer", "album": "Weezer", "year": "1994", "nav_id": "blue"},
            {"artist": "Weezer", "album": "Weezer", "year": "2019", "nav_id": "black"},
        ]
        lookup = build_collection_lookup(collection)

        match = find_collection_album("Weezer", "Weezer", lookup=lookup, year="2019")

        self.assertEqual(match["nav_id"], "black")

    def test_gap_targets_can_filter_artists(self):
        collection = [
            {"artist": "Matt Maltese", "album": "Krystal", "added_at": 10},
            {"artist": "Mac Miller", "album": "Swimming", "added_at": 20},
        ]

        targets = artist_gap_targets(collection, artist_filter="matt", max_artists=5)
        self.assertEqual([t["artist"] for t in targets], ["Matt Maltese"])

    def test_gap_targets_are_fuzzy_for_stylized_artists(self):
        collection = [
            {"artist": "$uicideboy$", "album": "Long Term Effects of Suffering", "added_at": 20},
            {"artist": "Suicidal Tendencies", "album": "Suicidal Tendencies", "added_at": 10},
        ]

        targets = artist_gap_targets(collection, artist_filter="$ucide", max_artists=5)
        self.assertEqual(targets[0]["artist"], "$uicideboy$")

    def test_missing_albums_excludes_owned_and_dedupes(self):
        owned = collection_keys([
            {"artist": "Mac Miller", "album": "Swimming"},
        ])
        candidates = [
            {"artist": "Mac Miller", "album": "Swimming"},
            {"artist": "Mac Miller", "album": "Circles (Demo)"},
            {"artist": "Mac Miller", "album": "Circles"},
            {"artist": "Mac Miller", "album": "Circles"},
            {"artist": "Mac Miller", "album": "Faces"},
        ]

        missing = missing_albums_for_artist("Mac Miller", candidates, owned, limit=6)
        self.assertEqual([a["album"] for a in missing], ["Circles", "Faces"])

    def test_discover_refresh_hooks_exist(self):
        router = (ROOT / "app" / "routers" / "discover.py").read_text()
        template = (ROOT / "app" / "templates" / "discover.html").read_text()

        self.assertIn('/api/refresh', router)
        self.assertIn('/api/state', router)
        self.assertIn('id="discover-refresh"', template)
        self.assertIn("setInterval(window.watchDiscoverState, 60000)", template)
        self.assertIn("/discover/api/refresh", template)
        self.assertIn("/discover/api/state", template)


if __name__ == "__main__":
    unittest.main()
