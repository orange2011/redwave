import unittest
from pathlib import Path


class HomeTemplateTests(unittest.TestCase):
    def test_red_top_uses_cover_fallback(self):
        template = Path("app/templates/home.html").read_text(encoding="utf-8")

        self.assertIn("/api/collection/cover_lfm?artist=", template)
        self.assertIn("data-fallback-cover", template)
        self.assertIn("fallback_cover", template)


if __name__ == "__main__":
    unittest.main()
