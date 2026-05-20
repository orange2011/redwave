import unittest

from app.routers.collection import _filter_albums, _sort_albums


class CollectionSortTests(unittest.TestCase):
    def test_collection_sort_modes_are_stable(self):
        albums = [
            {"artist": "Beta", "album": "Second", "year": "1999", "added_at": 2},
            {"artist": "Alpha", "album": "Third", "year": "2020", "added_at": 3},
            {"artist": "Alpha", "album": "First", "year": "2001", "added_at": 1},
        ]

        self.assertEqual([a["album"] for a in _sort_albums(albums, "album")], ["First", "Second", "Third"])
        self.assertEqual([a["album"] for a in _sort_albums(albums, "artist")], ["First", "Third", "Second"])
        self.assertEqual([a["album"] for a in _sort_albums(albums, "recent")], ["Third", "Second", "First"])
        self.assertEqual([a["album"] for a in _sort_albums(albums, "year_asc")], ["Second", "First", "Third"])

    def test_collection_filter_is_fuzzy_for_stylized_artists(self):
        albums = [
            {"artist": "Suicide Silence", "album": "The Cleansing"},
            {"artist": "$uicideboy$", "album": "I Want to Die in New Orleans"},
            {"artist": "Mac Miller", "album": "Swimming"},
        ]

        results = _filter_albums(albums, "$ucide")
        self.assertEqual(results[0]["artist"], "$uicideboy$")
        self.assertIn("Suicide Silence", [album["artist"] for album in results])

    def test_collection_filter_finds_album_typos(self):
        albums = [
            {"artist": "Ninajirachi", "album": "I Love My Computer"},
            {"artist": "Matt Maltese", "album": "Krystal"},
        ]

        results = _filter_albums(albums, "compter")
        self.assertEqual([album["album"] for album in results], ["I Love My Computer"])


if __name__ == "__main__":
    unittest.main()
