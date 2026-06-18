import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routers.home import home as home_route
from app.services import home_cache


class HomeTemplateTests(unittest.TestCase):
    def test_red_top_uses_cover_fallback(self):
        template = Path("app/templates/home.html").read_text(encoding="utf-8")

        self.assertIn("/api/collection/cover_lfm?artist=", template)
        self.assertIn("data-fallback-cover", template)
        self.assertIn("fallback_cover", template)

    def test_home_album_cards_have_cover_and_text_shadow(self):
        template = Path("app/templates/home.html").read_text(encoding="utf-8")

        self.assertIn("box-shadow:", template)
        self.assertIn("text-shadow:", template)

    def test_home_route_renders_cached_snapshot(self):
        snapshot = {
            "lastfm_albums": [],
            "recommendations": [],
            "lb_playlists": [],
            "collection": [{"artist": "Deary", "album": "Birding", "year": "2024"}],
            "recently_added": [{"artist": "Deary", "album": "Birding", "year": "2024"}],
            "red_top": [{"artist": "Deary", "album": "Birding", "year": "2024"}],
            "tracker_top_label": "OPS",
            "tracker_top_site": "https://orpheus.network",
            "rec_week": "2026-06-15",
        }

        with (
            patch("app.routers.home.schedule_home_cache_refresh_if_stale") as schedule,
            patch("app.routers.home.get_home_cache_snapshot", return_value=snapshot),
            patch("app.routers.home.templates.TemplateResponse", side_effect=lambda _name, context: context),
        ):
            context = asyncio.run(home_route(request=object()))

        schedule.assert_called_once()
        self.assertEqual(context["tracker_top_label"], "OPS")
        self.assertTrue(context["red_top"][0]["in_collection"])

    def test_home_cache_background_refresh_hooks_exist(self):
        main = Path("app/main.py").read_text(encoding="utf-8")

        self.assertIn("refresh_home_cache", main)
        self.assertIn("refresh_tracker_top_cache", main)
        self.assertIn("refresh_listenbrainz_cache", main)
        self.assertIn('"interval", minutes=15', main)
        self.assertIn('"cron", day_of_week="mon", hour=12, minute=15', main)

    def test_tracker_top_cache_ttl_is_daily(self):
        self.assertEqual(home_cache.TRACKER_TOP_TTL.total_seconds(), 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
