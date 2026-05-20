import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.routers.album import _tracker_album_title_hint


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


if __name__ == "__main__":
    unittest.main()
