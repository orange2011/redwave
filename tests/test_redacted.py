import asyncio
import unittest

import httpx

from app.config import settings
from app.services.redacted import (
    GazelleTrackerClient,
    TrackerRateLimitError,
    _TRACKER_BACKOFF_UNTIL,
    media_score_summary,
    normalize_media_preference,
    normalize_quality_profile,
    quality_profile_label,
    tracker_client_for,
    torrent_preference_sort_key,
    torrent_preference_score,
    torrent_matches_quality,
)


class RedactedQualityTests(unittest.TestCase):
    def test_quality_profiles_match_red_format_and_encoding(self):
        self.assertTrue(torrent_matches_quality({"format": "MP3", "encoding": "V0 (VBR)"}, "mp3_v0"))
        self.assertTrue(torrent_matches_quality({"format": "MP3", "encoding": "320"}, "mp3_320"))
        self.assertTrue(torrent_matches_quality({"format": "FLAC", "encoding": "24bit Lossless"}, "flac_24bit"))
        self.assertFalse(torrent_matches_quality({"format": "MP3", "encoding": "320"}, "mp3_v0"))
        self.assertFalse(torrent_matches_quality({"format": "FLAC", "encoding": "Lossless"}, "mp3_v0"))

    def test_unknown_quality_falls_back_to_any(self):
        self.assertEqual(normalize_quality_profile("wat"), "any")
        self.assertEqual(quality_profile_label("wat"), "Any quality")

    def test_media_preferences_include_red_media_values_for_legacy_envs(self):
        self.assertEqual(normalize_media_preference("blu-ray"), "blu_ray")

    def test_preference_sort_floats_cd_lossless_without_filtering(self):
        cd_flac = {"format": "FLAC", "encoding": "Lossless", "media": "CD"}
        web_flac = {"format": "FLAC", "encoding": "Lossless", "media": "WEB"}
        cd_mp3 = {"format": "MP3", "encoding": "V0 (VBR)", "media": "CD"}
        cassette_mp3 = {"format": "MP3", "encoding": "320", "media": "Cassette"}
        scores = {"CD": 100, "WEB": 50, "Vinyl": -10000, "Cassette": 0, "SACD": 90, "Blu-Ray": 80, "DVD": 70, "Soundboard": 20}

        rows = sorted(
            [cassette_mp3, web_flac, cd_mp3, cd_flac],
            key=lambda item: torrent_preference_sort_key(item, "flac_any", scores),
        )

        self.assertIs(rows[0], cd_flac)
        self.assertIn(web_flac, rows)
        self.assertIn(cassette_mp3, rows)

    def test_media_scores_can_promote_vinyl(self):
        cd_flac = {"format": "FLAC", "encoding": "Lossless", "media": "CD"}
        vinyl_flac = {"format": "FLAC", "encoding": "Lossless", "media": "Vinyl"}
        scores = {"CD": 10, "Vinyl": 500}

        self.assertGreater(
            torrent_preference_score(vinyl_flac, "flac_any", scores),
            torrent_preference_score(cd_flac, "flac_any", scores),
        )
        self.assertIn("Vinyl 500", media_score_summary(scores))

    def test_tracker_lookup_knows_red_and_ops(self):
        self.assertEqual(tracker_client_for("red").label, "RED")
        self.assertEqual(tracker_client_for("ops").label, "OPS")
        self.assertEqual(tracker_client_for("unknown").label, "RED")

    def test_tracker_rate_limit_response_sets_backoff(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            object.__setattr__(settings, "red_api_key", "token")
            _TRACKER_BACKOFF_UNTIL.pop("test-red", None)
            try:
                client._client = httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, json={
                            "status": "failure",
                            "error": "Your IP has been temporarily banned.",
                        })
                    )
                )
                with self.assertRaises(TrackerRateLimitError):
                    await client.search_torrents("Artist", "Album")
                with self.assertRaisesRegex(TrackerRateLimitError, "will not retry"):
                    await client.search_torrents("Artist", "Album")
            finally:
                await client._client.aclose()
                _TRACKER_BACKOFF_UNTIL.pop("test-red", None)
                object.__setattr__(settings, "red_api_key", old_key)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
