import unittest

from app.services.album_cache import _key, _matches_identity


class AlbumCacheTests(unittest.TestCase):
    def test_cache_key_uses_release_identity_for_self_titled_albums(self):
        blue = _key("Weezer", "Weezer", year="1994")
        black = _key("Weezer", "Weezer", year="2019")

        self.assertNotEqual(blue, black)

    def test_legacy_cache_entry_must_match_requested_year(self):
        self.assertTrue(_matches_identity({"year": "2019"}, year="2019"))
        self.assertFalse(_matches_identity({"year": "1994"}, year="2019"))


if __name__ == "__main__":
    unittest.main()
