import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.routers.album import _attach_global_track_popularity, _tracker_album_title_hint


class AlbumDetailTests(unittest.TestCase):
    def test_tracker_album_hint_uses_requested_year(self):
        fake_red = type("FakeRed", (), {})()
        fake_red.search_torrents = AsyncMock(return_value=[
            {"groupName": "Weezer", "groupYear": "1994"},
            {"groupName": "Weezer (Black Album)", "groupYear": "2019"},
        ])

        with patch("app.routers.album.red_client", fake_red):
            title = asyncio.run(_tracker_album_title_hint("Weezer", "Weezer", "2019"))

        self.assertEqual(title, "Weezer (Black Album)")

    def test_album_and_artist_lightboxes_use_hq_helper(self):
        album_template = Path("app/templates/album_detail.html").read_text(encoding="utf-8")
        artist_template = Path("app/templates/artist.html").read_text(encoding="utf-8")
        base_template = Path("app/templates/base.html").read_text(encoding="utf-8")

        self.assertIn("redwaveHighQualityImageUrl", base_template)
        self.assertIn("redwaveHighQualityImageUrl", album_template)
        self.assertIn("redwaveHighQualityImageUrl", artist_template)

    def test_global_track_popularity_is_attached_to_tracks(self):
        tracks, max_count = _attach_global_track_popularity(
            [{"name": "Birding"}, {"name": "Heavy Dream"}],
            {"birding": {"playcount": 12000, "listeners": 700}},
        )

        self.assertEqual(max_count, 12000)
        self.assertEqual([track["global_playcount"] for track in tracks], [12000, 0])
        self.assertEqual([track["global_playcount_display"] for track in tracks], ["12K", "0"])

    def test_album_template_has_cover_fallback_and_track_popularity_score(self):
        album_template = Path("app/templates/album_detail.html").read_text(encoding="utf-8")

        self.assertIn("redwaveCoverFallback", album_template)
        self.assertIn("/api/collection/cover_lfm", album_template)
        self.assertIn("detail-popularity-value", album_template)
        self.assertIn("track_popularity_max", album_template)
        self.assertIn("redwaveSortTracklist", album_template)
        self.assertIn("data-track-popularity", album_template)
        self.assertNotIn("detail-popularity-bar", album_template)


if __name__ == "__main__":
    unittest.main()
