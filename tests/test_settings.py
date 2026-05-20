import asyncio
import unittest
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
            patch.object(settings_page, "test_red", ok),
            patch.object(settings_page, "test_ops", fail),
        ):
            results = asyncio.run(settings_page._run_configured_save_checks({
                "RED_API_KEY": "configured",
                "OPS_API_KEY": "configured",
            }))

        by_label = {item["label"]: item for item in results}
        self.assertTrue(by_label["RED"]["ok"])
        self.assertFalse(by_label["OPS"]["ok"])
        self.assertIsNone(by_label["Navidrome"]["ok"])

    def test_settings_template_mentions_save_checks(self):
        with open("app/templates/settings.html", encoding="utf-8") as fh:
            template = fh.read()

        self.assertIn("tests every configured service", template)
        self.assertIn("Settings saved, but one or more checks failed", template)

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


if __name__ == "__main__":
    unittest.main()
