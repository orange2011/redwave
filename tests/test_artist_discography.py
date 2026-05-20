import unittest

from app.routers.artist import merge_artist_discography


class ArtistDiscographyTests(unittest.TestCase):
    def test_merges_cached_album_into_sparse_musicbrainz_discography(self):
        discography = {
            "Album": [
                {
                    "album": "Rokuyu (六喩)",
                    "year": "1975",
                    "cover_url": "https://example.invalid/rokuyu.jpg",
                    "mb_id": "mb-rokuyu",
                }
            ]
        }
        cached = [
            {
                "artist": "Jun Fukamachi",
                "album": "オン・ザ・ムーヴ",
                "year": "1978",
                "cover_url": "https://example.invalid/on-the-move.jpg",
            }
        ]

        merged = merge_artist_discography(
            discography,
            "Jun Fukamachi",
            collection=[],
            top_albums=[],
            cached_albums=cached,
        )

        self.assertEqual(
            [release["album"] for release in merged["Album"]],
            ["Rokuyu (六喩)", "オン・ザ・ムーヴ"],
        )

    def test_merges_collection_and_lastfm_without_duplicates(self):
        discography = {
            "Album": [
                {
                    "album": "Flying Beagle",
                    "year": "",
                    "cover_url": "",
                    "mb_id": "",
                }
            ]
        }
        collection = [
            {
                "artist": "Himiko Kikuchi",
                "album": "Flying Beagle",
                "year": "1987",
                "cover_url": "/api/navidrome/cover/flying",
            }
        ]
        top_albums = [
            {
                "artist": "Himiko Kikuchi",
                "album": "Flying Beagle",
                "cover_url": "https://example.invalid/flying.jpg",
            },
            {
                "artist": "Himiko Kikuchi",
                "album": "Sevilla Breeze",
                "cover_url": "https://example.invalid/sevilla.jpg",
            },
        ]

        merged = merge_artist_discography(
            discography,
            "Himiko Kikuchi",
            collection=collection,
            top_albums=top_albums,
            cached_albums=[],
        )

        self.assertEqual(
            [release["album"] for release in merged["Album"]],
            ["Flying Beagle", "Sevilla Breeze"],
        )
        self.assertEqual(merged["Album"][0]["year"], "1987")
        self.assertEqual(merged["Album"][0]["cover_url"], "/api/navidrome/cover/flying")


if __name__ == "__main__":
    unittest.main()
