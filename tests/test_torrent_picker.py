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
from app.services.torrent_meta import parse_torrent_manifest


def _bstr(value: str | bytes) -> bytes:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data


def _torrent_bytes(name: str, files: list[tuple[str, int]], pieces: bytes = b"x" * 20) -> bytes:
    encoded_files = b"".join(
        b"d6:lengthi" + str(size).encode("ascii") + b"e4:pathl" + _bstr(path) + b"ee"
        for path, size in files
    )
    return (
        b"d4:infod5:filesl" + encoded_files + b"e"
        + b"4:name" + _bstr(name)
        + b"12:piece lengthi16384e"
        + b"6:pieces" + _bstr(pieces)
        + b"ee"
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
        self.assertTrue(rows[0]["match_exact"])

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
        self.assertIn("Grab {{ t.tracker_label }}", template)
        self.assertIn("OPS safety check", template)
        self.assertIn("OPS check", template)
        self.assertIn("Grab RED + OPS check", template)
        self.assertIn("torrent-picker-overlay", template)
        self.assertIn("torrent-row:hover", template)
        self.assertIn("torrent-grab-button", template)
        self.assertNotIn("onmouseover=", template)
        self.assertNotIn("onmouseout=", template)
        self.assertIn("name=\"media\"", template)
        self.assertIn("name=\"remaster\"", template)
        self.assertIn("{{ t.match_label }}", template)

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
        selected_bytes = _torrent_bytes("Weezer", [("01 Track.mp3", 222)], pieces=b"m" * 20)
        fake_ops.search_torrents = AsyncMock(return_value=[
            {
                "artist": "Weezer",
                "groupName": "Weezer",
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
        fake_ops.get_torrent_file = AsyncMock(return_value=selected_bytes)
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
                    selected_format="MP3",
                    selected_encoding="320",
                    selected_media="WEB",
                    selected_manifest=parse_torrent_manifest(selected_bytes),
                ))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertIsNotNone(match)
        self.assertEqual(match["torrent_id"], 2)

    def test_ops_cross_seed_rejects_close_album_even_with_exact_size(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        fake_ops = type("FakeOps", (), {})()
        fake_ops.is_configured = lambda: True
        fake_ops.get_torrent_file = AsyncMock(return_value=_torrent_bytes("Deary", [("01 Birding.flac", 333)]))
        fake_ops.search_torrents = AsyncMock(return_value=[
            {
                "artist": "Deary",
                "groupName": "Birding Remixes",
                "groupYear": "2024",
                "groupId": 20,
                "_redwave_tracker": "ops",
                "_redwave_tracker_label": "OPS",
                "torrents": [
                    {"torrentId": 9, "format": "FLAC", "encoding": "Lossless", "media": "WEB", "size": 333},
                ],
            }
        ])
        try:
            with patch("app.routers.api.torrents.ops_client", fake_ops):
                match = asyncio.run(_find_ops_cross_seed_match(
                    "Deary",
                    "Birding",
                    "2024",
                    333,
                    "never",
                    "flac_any",
                    {"WEB": 50},
                    selected_format="FLAC",
                    selected_encoding="Lossless",
                    selected_media="WEB",
                    selected_manifest=parse_torrent_manifest(_torrent_bytes("Deary", [("01 Birding.flac", 333)])),
                ))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertIsNone(match)

    def test_ops_cross_seed_rejects_different_quality_even_with_exact_size(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        fake_ops = type("FakeOps", (), {})()
        fake_ops.is_configured = lambda: True
        fake_ops.get_torrent_file = AsyncMock(return_value=_torrent_bytes("Deary", [("01 Birding.flac", 444)]))
        fake_ops.search_torrents = AsyncMock(return_value=[
            {
                "artist": "Deary",
                "groupName": "Birding",
                "groupYear": "2024",
                "groupId": 21,
                "_redwave_tracker": "ops",
                "_redwave_tracker_label": "OPS",
                "torrents": [
                    {"torrentId": 10, "format": "MP3", "encoding": "320", "media": "WEB", "size": 444},
                ],
            }
        ])
        try:
            with patch("app.routers.api.torrents.ops_client", fake_ops):
                match = asyncio.run(_find_ops_cross_seed_match(
                    "Deary",
                    "Birding",
                    "2024",
                    444,
                    "never",
                    "flac_any",
                    {"WEB": 50},
                    selected_format="FLAC",
                    selected_encoding="Lossless",
                    selected_media="WEB",
                    selected_manifest=parse_torrent_manifest(_torrent_bytes("Deary", [("01 Birding.flac", 444)])),
                ))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertIsNone(match)

    def test_ops_cross_seed_rejects_different_file_manifest_even_with_exact_size(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        selected_bytes = _torrent_bytes("Bloodhound RED", [("01 Foxtrot Uniform Charlie Kilo.flac", 240409111)], pieces=b"r" * 20)
        ops_bytes = _torrent_bytes("Bloodhound OPS", [("01 Uhn Tiss Uhn Tiss Uhn Tiss.flac", 240409111)], pieces=b"o" * 20)
        fake_ops = type("FakeOps", (), {})()
        fake_ops.is_configured = lambda: True
        fake_ops.get_torrent_file = AsyncMock(return_value=ops_bytes)
        fake_ops.search_torrents = AsyncMock(return_value=[
            {
                "artist": "Bloodhound Gang",
                "groupName": "Hefty Fine",
                "groupYear": "2005",
                "groupId": 30,
                "_redwave_tracker": "ops",
                "_redwave_tracker_label": "OPS",
                "torrents": [
                    {"torrentId": 11, "format": "FLAC", "encoding": "Lossless", "media": "CD", "size": 240409111},
                ],
            }
        ])
        try:
            with patch("app.routers.api.torrents.ops_client", fake_ops):
                match = asyncio.run(_find_ops_cross_seed_match(
                    "Bloodhound Gang",
                    "Hefty Fine",
                    "2005",
                    240409111,
                    "never",
                    "flac_any",
                    {"CD": 100},
                    selected_format="FLAC",
                    selected_encoding="Lossless",
                    selected_media="CD",
                    selected_manifest=parse_torrent_manifest(selected_bytes),
                ))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertIsNone(match)

    def test_ops_cross_seed_returns_rename_map_for_same_payload_different_paths(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        selected_bytes = _torrent_bytes("Red Root", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"z" * 20)
        ops_bytes = _torrent_bytes("Ops Root", [
            ("01 - Track.flac", 100),
            ("02 - Track.flac", 200),
        ], pieces=b"z" * 20)
        fake_ops = type("FakeOps", (), {})()
        fake_ops.is_configured = lambda: True
        fake_ops.get_torrent_file = AsyncMock(return_value=ops_bytes)
        fake_ops.search_torrents = AsyncMock(return_value=[
            {
                "artist": "Deary",
                "groupName": "Birding",
                "groupYear": "2024",
                "groupId": 31,
                "_redwave_tracker": "ops",
                "_redwave_tracker_label": "OPS",
                "torrents": [
                    {"torrentId": 12, "format": "FLAC", "encoding": "Lossless", "media": "WEB", "size": 300},
                ],
            }
        ])
        try:
            with patch("app.routers.api.torrents.ops_client", fake_ops):
                match = asyncio.run(_find_ops_cross_seed_match(
                    "Deary",
                    "Birding",
                    "2024",
                    300,
                    "never",
                    "flac_any",
                    {"WEB": 50},
                    selected_format="FLAC",
                    selected_encoding="Lossless",
                    selected_media="WEB",
                    selected_manifest=parse_torrent_manifest(selected_bytes),
                ))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertIsNotNone(match)
        self.assertEqual(match["torrent_id"], 12)
        self.assertEqual(match["payload_match"]["match_mode"], "mapped-paths")
        self.assertEqual(match["payload_match"]["rename_map"], {
            "01 - Track.flac": "01 Track.flac",
            "02 - Track.flac": "02 Track.flac",
        })


if __name__ == "__main__":
    unittest.main()
