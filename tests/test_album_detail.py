import asyncio
import unittest
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


if __name__ == "__main__":
    unittest.main()
