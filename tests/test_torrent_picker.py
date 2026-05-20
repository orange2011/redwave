import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.routers.api.torrents import (
    _build_torrent_rows,
    _find_ops_cross_seed_match,
    _sort_torrent_rows,
    _source_note,
)


class TorrentPickerTests(unittest.TestCase):
    def test_rows_keep_tracker_metadata(self):
        groups = [
            {
                "artist": "Mac Miller",
                "groupName": "The Divine Feminine",
                "groupYear": "2016",
                "groupId": 123,
                "_redwave_tracker": "ops",
                "_redwave_tracker_label": "OPS",
                "_redwave_group_url": "https://orpheus.network/torrents.php?id=123",
                "torrents": [
                    {
                        "torrentId": 456,
                        "format": "FLAC",
                        "encoding": "Lossless",
                        "media": "CD",
                        "size": 123456789,
                        "seeders": 4,
                        "leechers": 0,
                    }
                ],
            }
        ]

        rows = _build_torrent_rows(
            groups,
            artist="Mac Miller",
            album="The Divine Feminine",
            year="2016",
            token_mode="preferred",
            quality_profile="flac_any",
            media_scores={"CD": 100},
        )

        self.assertEqual(rows[0]["tracker"], "ops")
        self.assertEqual(rows[0]["tracker_label"], "OPS")
        self.assertEqual(rows[0]["tracker_url"], "https://orpheus.network/torrents.php?id=123")
        self.assertFalse(rows[0]["will_use_token"])

    def test_rows_reject_same_artist_wrong_album_matches(self):
        groups = [
            {
                "artist": "Weezer",
                "groupName": "Weezer (Black Album)",
                "groupYear": "2019",
                "groupId": 1,
                "torrents": [{"torrentId": 10, "format": "FLAC", "encoding": "Lossless", "media": "CD"}],
            },
            {
                "artist": "Weezer",
                "groupName": "Pinkerton",
                "groupYear": "1996",
                "groupId": 2,
                "torrents": [{"torrentId": 20, "format": "FLAC", "encoding": "Lossless", "media": "CD"}],
            },
            {
                "artist": "Weezer",
                "groupName": "Weezer (Blue Album)",
                "groupYear": "1994",
                "groupId": 3,
                "torrents": [{"torrentId": 30, "format": "FLAC", "encoding": "Lossless", "media": "CD"}],
            },
        ]

        rows = _build_torrent_rows(
            groups,
            artist="Weezer",
            album="Weezer",
            year="2019",
            token_mode="never",
            quality_profile="flac_any",
            media_scores={"CD": 100},
        )

        self.assertEqual([row["red_group_id"] for row in rows], [1])

    def test_picker_has_sortable_peer_and_quality_columns(self):
        template = Path("app/templates/partials/torrent_picker.html").read_text(encoding="utf-8")

        for column in ("source", "age", "title", "size", "peers", "quality", "token"):
            self.assertIn(f"redwaveSortTorrentPicker(this, '{column}')", template)

        self.assertIn("redwaveSortTorrentPicker(this, 'peers')", template)
        self.assertIn("redwaveSortTorrentPicker(this, 'quality')", template)
        self.assertIn("data-tracker-label", template)
        self.assertIn("data-age-days", template)
        self.assertIn("data-sort-title", template)
        self.assertIn("data-size-bytes", template)
        self.assertIn("data-quality-score", template)
        self.assertIn("data-seeders", template)
        self.assertIn("data-token-score", template)
        self.assertIn("torrent-sort-arrow", template)
        self.assertIn("defaultDirections", template)

    def test_combined_tracker_rows_prefer_red_before_equal_ops(self):
        rows = [
            {
                "tracker": "ops",
                "format": "FLAC",
                "encoding": "Lossless",
                "media": "CD",
                "match_score": 23,
                "seeders": 200,
            },
            {
                "tracker": "red",
                "format": "FLAC",
                "encoding": "Lossless",
                "media": "CD",
                "match_score": 23,
                "seeders": 1,
            },
        ]

        sorted_rows = _sort_torrent_rows(rows, "flac_any", {"CD": 100})

        self.assertEqual([row["tracker"] for row in sorted_rows], ["red", "ops"])

    def test_source_note_mentions_both_trackers(self):
        self.assertEqual(
            _source_note(4, 2, True),
            "Showing RED and OPS results (4 RED, 2 OPS).",
        )

    def test_ops_cross_seed_match_uses_exact_size(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        fake_ops = type("FakeOps", (), {})()
        fake_ops.is_configured = lambda: True
        fake_ops.search_torrents = AsyncMock(return_value=[
            {
                "artist": "Weezer",
                "groupName": "Weezer (Black Album)",
                "groupYear": "2019",
                "groupId": 10,
                "_redwave_tracker": "ops",
                "_redwave_tracker_label": "OPS",
                "_redwave_group_url": "https://orpheus.network/torrents.php?id=10",
                "torrents": [
                    {"torrentId": 1, "format": "FLAC", "encoding": "Lossless", "media": "CD", "size": 111},
                    {"torrentId": 2, "format": "MP3", "encoding": "320", "media": "WEB", "size": 222},
                ],
            }
        ])
        try:
            with patch("app.routers.api.torrents.ops_client", fake_ops):
                match = asyncio.run(_find_ops_cross_seed_match(
                    "Weezer",
                    "Weezer",
                    "2019",
                    222,
                    "never",
                    "flac_any",
                    {"CD": 100, "WEB": 50},
                ))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertIsNotNone(match)
        self.assertEqual(match["torrent_id"], 2)


if __name__ == "__main__":
    unittest.main()
