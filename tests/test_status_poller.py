import unittest

from app.tasks.status_poller import _find_by_hash, _is_completed_state, _merge_torrents


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


if __name__ == "__main__":
    unittest.main()
