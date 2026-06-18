import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.tasks.status_poller import (
    _add_pending_cross_seed,
    _add_pending_ops_cross_seed,
    _find_by_hash,
    _is_completed_state,
    _merge_torrents,
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


class AddResult:
    hashes = ["opshash"]

    def __bool__(self):
        return True


class StatusPollerTests(unittest.TestCase):
    def test_completed_states_include_common_qbittorrent_upload_states(self):
        for state in ("uploading", "pausedUP", "stoppedUP", "seeding", "forcedUP", "queuedUP", "stalledUP", "checkingUP"):
            self.assertTrue(_is_completed_state(state), state)

        self.assertFalse(_is_completed_state("downloading"))
        self.assertFalse(_is_completed_state("stalledDL"))

    def test_hash_matching_is_case_insensitive(self):
        torrent = _find_by_hash([
            {"hash": "ABC123", "name": "Test"},
        ], "abc123")

        self.assertEqual(torrent["name"], "Test")

    def test_merge_torrents_keeps_category_and_all_fallback_without_duplicates(self):
        merged = _merge_torrents(
            [{"hash": "one", "name": "From category"}],
            [{"hash": "one", "name": "Duplicate"}, {"hash": "two", "name": "From all"}],
        )

        self.assertEqual([torrent["name"] for torrent in merged], ["From category", "From all"])

    def test_legacy_pending_ops_cross_seed_is_skipped(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        option = type("Option", (), {
            "raw_json": json.dumps({
                "tracker": "red",
                "cross_seed": {
                    "ops": {
                        "status": "pending",
                        "torrent_id": 123,
                    }
                },
            })
        })()
        try:
            added = asyncio.run(_add_pending_ops_cross_seed(option))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        payload = json.loads(option.raw_json)
        self.assertFalse(added)
        self.assertEqual(payload["cross_seed"]["ops"]["status"], "skipped")

    def test_pending_ops_cross_seed_applies_safe_rename_map(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        red_bytes = _torrent_bytes("Red Root", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"z" * 20)
        ops_bytes = _torrent_bytes("Ops Root", [
            ("01 - Track.flac", 100),
            ("02 - Track.flac", 200),
        ], pieces=b"z" * 20)
        red_manifest = parse_torrent_manifest(red_bytes)
        ops_manifest = parse_torrent_manifest(ops_bytes)
        option = type("Option", (), {
            "raw_json": json.dumps({
                "tracker": "red",
                "selected": {
                    "torrent_manifest": red_manifest.to_dict(),
                },
                "cross_seed": {
                    "ops": {
                        "status": "pending",
                        "torrent_id": 321,
                        "match_policy": "payload-map-v2",
                        "rename_map": {
                            "01 - Track.flac": "01 Track.flac",
                            "02 - Track.flac": "02 Track.flac",
                        },
                        "torrent_manifest": ops_manifest.to_dict(),
                    }
                },
            })
        })()
        fake_red = type("FakeRed", (), {"label": "RED"})()
        fake_ops = type("FakeOps", (), {"label": "OPS"})()
        fake_ops.get_torrent_file = AsyncMock(return_value=ops_bytes)
        fake_qbt = type("FakeQbt", (), {})()
        fake_qbt.add_torrent_with_result = AsyncMock(return_value=AddResult())
        fake_qbt.rename_torrent = AsyncMock()
        fake_qbt.rename_folder = AsyncMock()
        fake_qbt.rename_file = AsyncMock()
        fake_qbt.recheck_torrent = AsyncMock()
        fake_qbt.resume_torrent = AsyncMock()
        client_for = lambda name: fake_ops if name == "ops" else fake_red
        try:
            with (
                patch("app.tasks.status_poller.tracker_client_for", side_effect=client_for),
                patch("app.tasks.status_poller.qbt_client", fake_qbt),
            ):
                added = asyncio.run(_add_pending_ops_cross_seed(option, {
                    "save_path": "/downloads/music",
                    "content_path": "/downloads/music/Red Root",
                }))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertTrue(added)
        fake_qbt.add_torrent_with_result.assert_awaited_once()
        kwargs = fake_qbt.add_torrent_with_result.await_args.kwargs
        self.assertEqual(kwargs["save_path"], "/downloads/music")
        self.assertEqual(kwargs["content_layout"], "Original")
        self.assertTrue(kwargs["paused"])
        fake_qbt.rename_torrent.assert_awaited_once_with("opshash", "Red Root")
        fake_qbt.rename_folder.assert_awaited_once_with("opshash", "Ops Root", "Red Root")
        fake_qbt.rename_file.assert_any_await("opshash", "Red Root/01 - Track.flac", "Red Root/01 Track.flac")
        fake_qbt.rename_file.assert_any_await("opshash", "Red Root/02 - Track.flac", "Red Root/02 Track.flac")
        fake_qbt.recheck_torrent.assert_awaited_once_with("opshash")
        fake_qbt.resume_torrent.assert_awaited_once_with("opshash")

    def test_pending_red_cross_seed_applies_safe_rename_map(self):
        old_value = settings.ops_cross_seed
        object.__setattr__(settings, "ops_cross_seed", "1")
        ops_bytes = _torrent_bytes("Ops Root", [
            ("01 - Track.flac", 100),
            ("02 - Track.flac", 200),
        ], pieces=b"z" * 20)
        red_bytes = _torrent_bytes("Red Root", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"z" * 20)
        ops_manifest = parse_torrent_manifest(ops_bytes)
        red_manifest = parse_torrent_manifest(red_bytes)
        option = type("Option", (), {
            "raw_json": json.dumps({
                "tracker": "ops",
                "selected": {
                    "tracker": "ops",
                    "torrent_manifest": ops_manifest.to_dict(),
                },
                "cross_seed": {
                    "red": {
                        "status": "pending",
                        "torrent_id": 654,
                        "match_policy": "payload-map-v2",
                        "rename_map": {
                            "01 Track.flac": "01 - Track.flac",
                            "02 Track.flac": "02 - Track.flac",
                        },
                        "torrent_manifest": red_manifest.to_dict(),
                    }
                },
            })
        })()
        fake_red = type("FakeRed", (), {"label": "RED"})()
        fake_red.get_torrent_file = AsyncMock(return_value=red_bytes)
        fake_ops = type("FakeOps", (), {"label": "OPS"})()
        fake_qbt = type("FakeQbt", (), {})()
        fake_qbt.add_torrent_with_result = AsyncMock(return_value=AddResult())
        fake_qbt.rename_torrent = AsyncMock()
        fake_qbt.rename_folder = AsyncMock()
        fake_qbt.rename_file = AsyncMock()
        fake_qbt.recheck_torrent = AsyncMock()
        fake_qbt.resume_torrent = AsyncMock()
        client_for = lambda name: fake_red if name == "red" else fake_ops
        try:
            with (
                patch("app.tasks.status_poller.tracker_client_for", side_effect=client_for),
                patch("app.tasks.status_poller.qbt_client", fake_qbt),
            ):
                added = asyncio.run(_add_pending_cross_seed(option, "red", {
                    "save_path": "/downloads/music",
                    "content_path": "/downloads/music/Ops Root",
                }))
        finally:
            object.__setattr__(settings, "ops_cross_seed", old_value)

        self.assertTrue(added)
        kwargs = fake_qbt.add_torrent_with_result.await_args.kwargs
        self.assertEqual(kwargs["tags"], [settings.qbt_red_tag])
        self.assertEqual(kwargs["save_path"], "/downloads/music")
        self.assertEqual(kwargs["content_layout"], "Original")
        self.assertTrue(kwargs["paused"])
        fake_qbt.rename_torrent.assert_awaited_once_with("opshash", "Ops Root")
        fake_qbt.rename_folder.assert_awaited_once_with("opshash", "Red Root", "Ops Root")
        fake_qbt.rename_file.assert_any_await("opshash", "Ops Root/01 Track.flac", "Ops Root/01 - Track.flac")
        fake_qbt.rename_file.assert_any_await("opshash", "Ops Root/02 Track.flac", "Ops Root/02 - Track.flac")
        fake_qbt.recheck_torrent.assert_awaited_once_with("opshash")
        fake_qbt.resume_torrent.assert_awaited_once_with("opshash")


if __name__ == "__main__":
    unittest.main()
