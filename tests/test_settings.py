import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import settings_page
from app.services.qbittorrent import QBittorrentClient, qbt_base_url, qbt_login_error_message


class SettingsTests(unittest.TestCase):
    def test_musicdir_warns_about_docker_volume_mapping(self):
        old_value = settings.music_dir
        object.__setattr__(settings, "music_dir", "/data/media/music:/music:ro")
        try:
            response = asyncio.run(settings_page.test_musicdir())
            data = asyncio.run(settings_page._response_json(response))
        finally:
            object.__setattr__(settings, "music_dir", old_value)

        self.assertFalse(data["ok"])
        self.assertIn("Docker volume mapping", data["msg"])
        self.assertIn("/music", data["msg"])

    def test_save_checks_skip_unconfigured_services_and_report_failures(self):
        async def ok():
            return JSONResponse({"ok": True, "msg": "Connected"})

        async def fail():
            return JSONResponse({"ok": False, "msg": "Nope"})

        with (
            patch.object(settings_page, "test_lastfm", ok),
            patch.object(settings_page, "test_discogs", fail),
        ):
            results = asyncio.run(settings_page._run_configured_save_checks({
                "RED_API_KEY": "configured",
                "LASTFM_API_KEY": "configured",
                "LASTFM_USERNAME": "configured",
                "DISCOGS_TOKEN": "configured",
            }))

        by_label = {item["label"]: item for item in results}
        self.assertIsNone(by_label["RED"]["ok"])
        self.assertIn("avoid tracker rate limits", by_label["RED"]["msg"])
        self.assertTrue(by_label["Last.fm"]["ok"])
        self.assertFalse(by_label["Discogs"]["ok"])
        self.assertIsNone(by_label["Navidrome"]["ok"])

    def test_settings_template_mentions_save_checks(self):
        with open("app/templates/settings.html", encoding="utf-8") as fh:
            template = fh.read()

        self.assertIn("tests safe configured services", template)
        self.assertIn("Settings saved, but one or more checks failed", template)

    def test_write_env_preserves_local_shape_and_backup(self):
        old_path = settings_page.ENV_PATH
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# Redwave local config\n"
                "APP_USERNAME=mine\n"
                "LIDARR_URL=http://old\n"
                "\n"
                "RED_API_KEY=old\n"
                "CUSTOM_LOCAL_KEY=keep-me\n",
                encoding="utf-8",
            )
            settings_page.ENV_PATH = env_path
            try:
                settings_page._write_env(
                    {"RED_API_KEY": "new", "APP_THEME": "black"},
                    remove_keys={"LIDARR_URL"},
                )
            finally:
                settings_page.ENV_PATH = old_path

            text = env_path.read_text(encoding="utf-8")
            backup = (env_path.parent / ".env.bak").read_text(encoding="utf-8")

        self.assertIn("# Redwave local config", text)
        self.assertIn("APP_USERNAME=mine", text)
        self.assertIn("CUSTOM_LOCAL_KEY=keep-me", text)
        self.assertIn("RED_API_KEY=new", text)
        self.assertIn("APP_THEME=black", text)
        self.assertNotIn("LIDARR_URL", text)
        self.assertIn("LIDARR_URL=http://old", backup)

    def test_settings_page_uses_live_settings_when_env_file_is_sparse(self):
        old_path = settings_page.ENV_PATH
        old_theme = settings.app_theme
        old_navidrome = settings.navidrome_url
        old_qbt = settings.qbt_host
        with tempfile.TemporaryDirectory() as tmp:
            settings_page.ENV_PATH = Path(tmp) / ".env"
            object.__setattr__(settings, "app_theme", "black")
            object.__setattr__(settings, "navidrome_url", "http://navidrome:4533")
            object.__setattr__(settings, "qbt_host", "http://qbittorrent:8080")
            try:
                env = settings_page._env_with_live_settings_defaults()
            finally:
                settings_page.ENV_PATH = old_path
                object.__setattr__(settings, "app_theme", old_theme)
                object.__setattr__(settings, "navidrome_url", old_navidrome)
                object.__setattr__(settings, "qbt_host", old_qbt)

        self.assertEqual(env["APP_THEME"], "black")
        self.assertEqual(env["NAVIDROME_URL"], "http://navidrome:4533")
        self.assertEqual(env["QBT_HOST"], "http://qbittorrent:8080")

    def test_tracker_rate_limit_detection(self):
        self.assertTrue(settings_page._looks_like_tracker_rate_limit("Your IP has been temporarily banned."))
        self.assertTrue(settings_page._looks_like_tracker_rate_limit("Too many requests"))
        self.assertFalse(settings_page._looks_like_tracker_rate_limit("Invalid API key"))

    def test_qbittorrent_base_url_keeps_reverse_proxy_path(self):
        old_value = settings.qbt_host
        object.__setattr__(settings, "qbt_host", "example.test/qbt/")
        try:
            self.assertEqual(qbt_base_url(), "http://example.test/qbt")
        finally:
            object.__setattr__(settings, "qbt_host", old_value)

    def test_qbittorrent_login_error_is_actionable(self):
        response = httpx.Response(403, text="")
        msg = qbt_login_error_message(response)
        self.assertIn("HTTP 403", msg)
        self.assertIn("Docker", msg)

    def test_qbittorrent_login_accepts_auth_bypass_probe(self):
        async def run():
            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/auth/login"):
                    return httpx.Response(403, text="")
                if request.url.path.endswith("/app/version"):
                    return httpx.Response(200, text="v5.0.4")
                return httpx.Response(404)

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                await QBittorrentClient().login(client, "http://qbt.local")

        asyncio.run(run())

    def test_qbittorrent_login_raises_clear_error(self):
        async def run():
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(403, text="")

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with self.assertRaisesRegex(Exception, "Login failed"):
                    await QBittorrentClient().login(client, "http://qbt.local")

        asyncio.run(run())

    def test_qbittorrent_accepts_json_add_success_and_hash(self):
        response = httpx.Response(200, json={
            "added_torrent_ids": ["9d74c3427763bbac0abc9ec0f7feab12bcfa0eaa"],
            "failure_count": 0,
            "pending_count": 0,
            "success_count": 1,
        })

        result = QBittorrentClient().parse_add_response(response)

        self.assertTrue(result)
        self.assertEqual(result.hashes, ["9d74c3427763bbac0abc9ec0f7feab12bcfa0eaa"])


if __name__ == "__main__":
    unittest.main()
