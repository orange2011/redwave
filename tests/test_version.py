import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.version import get_app_version


class VersionTests(unittest.TestCase):
    def tearDown(self):
        get_app_version.cache_clear()

    def test_env_version_wins(self):
        with patch.dict("os.environ", {
            "REDWAVE_VERSION": "r999.test",
            "REDWAVE_COMMIT": "abcdef",
            "REDWAVE_BRANCH": "main",
        }, clear=False):
            get_app_version.cache_clear()
            info = get_app_version()

        self.assertEqual(info["version"], "r999.test")
        self.assertEqual(info["commit"], "abcdef")
        self.assertEqual(info["branch"], "main")
        self.assertEqual(info["source"], "env")

    def test_version_file_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "redwave-version.json"
            path.write_text(json.dumps({
                "version": "r123.abc1234",
                "revision": "123",
                "commit": "abc1234def",
                "short_commit": "abc1234",
                "branch": "fix-red-search-fallbacks",
                "source": "docker",
            }), encoding="utf-8")
            with patch.dict("os.environ", {"REDWAVE_VERSION_FILE": str(path)}, clear=False):
                get_app_version.cache_clear()
                info = get_app_version()

        self.assertEqual(info["version"], "r123.abc1234")
        self.assertEqual(info["short_commit"], "abc1234")
        self.assertEqual(info["source"], "docker")

    def test_settings_template_displays_app_version(self):
        template = Path("app/templates/settings.html").read_text(encoding="utf-8")

        self.assertIn("app_version.version", template)
        self.assertIn("app_version.short_commit", template)


if __name__ == "__main__":
    unittest.main()
