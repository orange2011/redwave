import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.config import settings
from app.services.redacted import (
    GazelleTrackerClient,
    TrackerRateLimitError,
    _TRACKER_BACKOFF_UNTIL,
    _track_search_titles,
    media_score_summary,
    normalize_media_preference,
    normalize_quality_profile,
    normalize_tracker_mode,
    ordered_tracker_names,
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

    def test_tracker_modes_and_primary_order(self):
        self.assertEqual(normalize_tracker_mode("unknown"), "both")
        self.assertEqual(ordered_tracker_names("red", "ops"), ["red"])
        self.assertEqual(ordered_tracker_names("ops", "red"), ["ops"])
        self.assertEqual(ordered_tracker_names("both", "ops"), ["ops", "red"])

    def test_ops_uses_recommended_token_authorization_header(self):
        client = GazelleTrackerClient(
            tracker="ops",
            label="OPS",
            base_url="https://ops.test/ajax.php",
            site_url="https://ops.test",
            api_key_attr="ops_api_key",
        )
        old_key = settings.ops_api_key
        object.__setattr__(settings, "ops_api_key", "secret")
        try:
            self.assertEqual(client.authorization_header, "token secret")
        finally:
            asyncio.run(client._client.aclose())
            object.__setattr__(settings, "ops_api_key", old_key)

    def test_ops_preferred_token_retries_without_usetoken_parameter(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="ops",
                label="OPS",
                base_url="https://ops.test/ajax.php",
                site_url="https://ops.test",
                api_key_attr="ops_api_key",
            )
            old_key = settings.ops_api_key
            old_client = client._client
            object.__setattr__(settings, "ops_api_key", "secret")
            requests = []

            def handler(request):
                requests.append(request)
                if len(requests) == 1:
                    return httpx.Response(200, content=b"You do not have any freeleech tokens left.")
                return httpx.Response(200, content=b"d4:infode")

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                content = await client.get_torrent_file(42, use_token=True, token_mode="preferred")
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "ops_api_key", old_key)

            self.assertEqual(content, b"d4:infode")
            self.assertEqual(requests[0].url.params.get("usetoken"), "1")
            self.assertNotIn("usetoken", requests[1].url.params)
            self.assertEqual(requests[0].headers["Authorization"], "token secret")

        asyncio.run(run())

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

    def test_track_search_titles_prefers_distinctive_tracks(self):
        titles = _track_search_titles([
            {"name": "Go"},
            {"name": "Hull az elsargult level"},
            {"name": "Elmegyek"},
            {"name": "Elmegyek"},
        ], album="Elmegyek", max_tracks=2)

        self.assertEqual(titles, ["Elmegyek", "Hull az elsargult level"])

    def test_search_torrents_falls_back_to_artistname(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red-search",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            old_client = client._client
            object.__setattr__(settings, "red_api_key", "token")

            def handler(request):
                if request.url.params.get("artistname") == "TRESPASSER":
                    return httpx.Response(200, json={"status": "success", "response": {"results": [{
                        "artist": "TRESPASSER",
                        "groupName": "יְהִי אוֹר",
                        "groupYear": "2026",
                        "groupId": 2664318,
                        "torrents": [{"torrentId": 10}],
                    }]}})
                return httpx.Response(200, json={"status": "success", "response": {"results": []}})

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                groups = await client.search_torrents("TRESPASSER", "יְהִי אוֹר")
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "red_api_key", old_key)

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["groupId"], 2664318)

        asyncio.run(run())

    def test_track_fallback_search_aggregates_multiple_track_hits(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red-track",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            old_client = client._client
            object.__setattr__(settings, "red_api_key", "token")

            group = {
                "artist": "Mate Peter",
                "groupName": "A track-hosting release",
                "groupYear": "1997",
                "groupId": 2591442,
                "torrents": [{"torrentId": 99, "format": "FLAC", "encoding": "Lossless", "media": "CD"}],
            }

            def handler(request):
                search = request.url.params.get("filelist", "")
                results = [group] if ("Elmegyek" in search or "Most elsz" in search) else []
                return httpx.Response(200, json={"status": "success", "response": {"results": results}})

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with patch("app.services.redacted.TRACKER_REQUEST_SPACING_SECONDS", 0):
                    matches = await client.search_torrents_by_tracks(
                        "Mate Peter",
                        "Different Album",
                        [{"name": "Elmegyek"}, {"name": "Most elsz"}, {"name": "No match"}],
                    )
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "red_api_key", old_key)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["groupId"], 2591442)
            self.assertEqual(matches[0]["_redwave_search_mode"], "track_fallback")
            self.assertCountEqual(matches[0]["_redwave_track_hits"], ["Elmegyek", "Most elsz"])
            self.assertEqual(matches[0]["_redwave_group_url"], "https://redacted.test/torrents.php?id=2591442")

        asyncio.run(run())

    def test_track_fallback_accepts_artist_title_track_single_hit(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red-track",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            old_client = client._client
            object.__setattr__(settings, "red_api_key", "token")

            def handler(request):
                results = [{
                    "artist": "Máté Péter",
                    "groupName": "Vallomások",
                    "groupYear": "2001",
                    "groupId": 2591442,
                    "torrents": [{"torrentId": 99}],
                }] if request.url.params.get("filelist") == "Elmegyek" else []
                return httpx.Response(200, json={"status": "success", "response": {"results": results}})

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with patch("app.services.redacted.TRACKER_REQUEST_SPACING_SECONDS", 0):
                    matches = await client.search_torrents_by_tracks(
                        "Mate Peter",
                        "Elmegyek",
                        [{"name": "Elmegyek"}, {"name": "No match"}],
                    )
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "red_api_key", old_key)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["groupId"], 2591442)
            self.assertEqual(matches[0]["_redwave_track_hits"], ["Elmegyek"])

        asyncio.run(run())

    def test_track_fallback_rejects_single_track_wrong_artist(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red-track",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            old_client = client._client
            object.__setattr__(settings, "red_api_key", "token")

            def handler(request):
                search = request.url.params.get("filelist", "")
                results = [{
                    "artist": "The Kid Laroi",
                    "groupName": "BEFORE I FORGET",
                    "groupYear": "2026",
                    "groupId": 266,
                    "torrents": [{"torrentId": 99}],
                }] if "I Love You So" in search else []
                return httpx.Response(200, json={"status": "success", "response": {"results": results}})

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with patch("app.services.redacted.TRACKER_REQUEST_SPACING_SECONDS", 0):
                    matches = await client.search_torrents_by_tracks(
                        "The Walters",
                        "I Love You So",
                        [{"name": "I Love You So"}],
                    )
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "red_api_key", old_key)

            self.assertEqual(matches, [])

        asyncio.run(run())

    def test_track_fallback_allows_single_track_under_various_artists_when_artist_scoped(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red-track",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            old_client = client._client
            object.__setattr__(settings, "red_api_key", "token")

            def handler(request):
                results = [{
                    "artist": "Various Artists",
                    "groupName": "A Carefully Named Compilation",
                    "groupYear": "2014",
                    "groupId": 267,
                    "torrents": [{"torrentId": 100}],
                }] if request.url.params.get("filelist") == "The Walters I Love You So" else []
                return httpx.Response(200, json={"status": "success", "response": {"results": results}})

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with patch("app.services.redacted.TRACKER_REQUEST_SPACING_SECONDS", 0):
                    matches = await client.search_torrents_by_tracks(
                        "The Walters",
                        "I Love You So",
                        [{"name": "I Love You So"}],
                    )
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "red_api_key", old_key)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["groupId"], 267)
            self.assertTrue(matches[0]["_redwave_track_artist_confirmed"])

        asyncio.run(run())

    def test_track_fallback_search_rejects_single_hit_when_more_tracks_were_checked(self):
        async def run():
            client = GazelleTrackerClient(
                tracker="test-red-track",
                label="RED",
                base_url="https://redacted.test/ajax.php",
                site_url="https://redacted.test",
                api_key_attr="red_api_key",
            )
            old_key = settings.red_api_key
            old_client = client._client
            object.__setattr__(settings, "red_api_key", "token")

            def handler(request):
                search = request.url.params.get("filelist", "")
                results = [{
                    "artist": "Mate Peter",
                    "groupName": "One stray track",
                    "groupYear": "1997",
                    "groupId": 10,
                    "torrents": [{"torrentId": 1}],
                }] if "Elmegyek" in search else []
                return httpx.Response(200, json={"status": "success", "response": {"results": results}})

            try:
                client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with patch("app.services.redacted.TRACKER_REQUEST_SPACING_SECONDS", 0):
                    matches = await client.search_torrents_by_tracks(
                        "Mate Peter",
                        "Different Album",
                        [{"name": "Elmegyek"}, {"name": "Most elsz"}],
                    )
            finally:
                await client._client.aclose()
                await old_client.aclose()
                object.__setattr__(settings, "red_api_key", old_key)

            self.assertEqual(matches, [])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
