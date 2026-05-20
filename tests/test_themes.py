from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ThemeConfigTests(unittest.TestCase):
    def test_base_template_declares_supported_themes(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text()
        self.assertIn('["redwave", "black", "light"]', base)
        self.assertIn(".theme-redwave", base)
        self.assertIn(".theme-black", base)
        self.assertIn(".theme-light", base)
        self.assertIn("Dark-down.svg", base)
        self.assertIn("Light-down.svg", base)
        self.assertIn("toast-stack", base)
        self.assertIn("showToast", base)

    def test_detail_pages_keep_collection_nav_active(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text()
        self.assertIn("current_path.startswith('/artist')", base)
        self.assertIn("current_path.startswith('/album')", base)
        self.assertIn("'active' if collection_active else ''", base)

    def test_mobile_nav_button_stays_inside_mobile_grid(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text()
        self.assertIn(".nav-shell", base)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", base)
        self.assertIn("grid-template-columns: minmax(8rem, 1fr) auto minmax(16rem, 1fr)", base)
        self.assertIn('aria-controls="mobile-menu"', base)
        self.assertIn('aria-expanded="false"', base)
        self.assertIn("toggleMobileMenu", base)
        self.assertIn("mobile-menu.is-open", base)
        self.assertIn("<span>Menu</span>", base)
        self.assertIn('id="mobile-menu"', base)

    def test_login_uses_theme_system(self):
        login = (ROOT / "app" / "templates" / "login.html").read_text()
        self.assertIn('theme-{{ app_theme }}', login)
        self.assertIn(".theme-redwave", login)
        self.assertIn(".theme-black", login)
        self.assertIn(".theme-light", login)
        self.assertIn("Dark-down.svg", login)
        self.assertIn("Light-down.svg", login)

    def test_settings_accepts_only_supported_themes(self):
        settings_page = (ROOT / "app" / "routers" / "settings_page.py").read_text()
        self.assertIn('{"redwave", "black", "light"}', settings_page)
        self.assertIn('"APP_THEME"', settings_page)
        self.assertIn('"RED_QUALITY_PROFILE"', settings_page)
        self.assertIn('"OPS_API_KEY"', settings_page)
        self.assertIn('"OPS_CROSS_SEED"', settings_page)
        self.assertIn('"QBT_RED_TAG"', settings_page)
        self.assertIn('"QBT_OPS_TAG"', settings_page)
        self.assertIn('"RED_MEDIA_SCORE_CD"', settings_page)
        self.assertIn('"RED_MEDIA_SCORE_BLU_RAY"', settings_page)

    def test_settings_template_lists_theme_options(self):
        settings_template = (ROOT / "app" / "templates" / "settings.html").read_text()
        self.assertIn('("redwave"', settings_template)
        self.assertIn('("black"', settings_template)
        self.assertIn('("light"', settings_template)
        self.assertIn('"RED_QUALITY_PROFILE"', settings_template)
        self.assertIn('"OPS_API_KEY"', settings_template)
        self.assertIn("OPS Fallback", settings_template)
        self.assertIn('"OPS_CROSS_SEED"', settings_template)
        self.assertIn('"QBT_RED_TAG"', settings_template)
        self.assertIn('"QBT_OPS_TAG"', settings_template)
        self.assertIn("Media Scores", settings_template)
        self.assertIn('("mp3_v0"', settings_template)
        self.assertIn('("mp3_320"', settings_template)
        self.assertIn("score_field", settings_template)
        self.assertIn("/search/diagnostics", settings_template)

    def test_search_diagnostics_route_and_template_exist(self):
        router = (ROOT / "app" / "routers" / "search.py").read_text()
        template = (ROOT / "app" / "templates" / "search_diagnostics.html").read_text()
        self.assertIn('/search/diagnostics', router)
        self.assertIn("Search Diagnostics", template)
        self.assertIn("normalized", template)


if __name__ == "__main__":
    unittest.main()
