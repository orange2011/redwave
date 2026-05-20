import argparse
import asyncio
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _active_nav(html: str) -> str:
    match = re.search(r'<a[^>]+class="[^"]*\bnav-link\b[^"]*\bactive\b[^"]*"[^>]*>([^<]+)</a>', html)
    return match.group(1).strip() if match else ""


def _assert_contains(label: str, body: str, expected: str) -> tuple[bool, str]:
    ok = expected in body
    return ok, f"{label}: expected text {expected!r}"


async def _login(client: httpx.AsyncClient, base_url: str) -> None:
    await client.post(
        f"{base_url}/login",
        data={"username": settings.app_username, "password": settings.app_password},
        follow_redirects=True,
    )


async def run(base_url: str) -> int:
    checks: list[tuple[bool, str]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        login = await client.get(f"{base_url}/login")
        login.raise_for_status()
        checks.append(_assert_contains("login theme", login.text, f"theme-{settings.app_theme}"))
        checks.append(_assert_contains("login card", login.text, "login-card"))

        await _login(client, base_url)
        pages = [
            ("home", "/", "Home", "Weekly Exploration"),
            ("search", "/search?q=The%20Divine%20Feminine", "Search", "Top Result"),
            ("search diagnostics", "/search/diagnostics?q=The%20Divine%20Feminine", "Search", "Search Diagnostics"),
            ("discover genres", "/discover/genres?genre=metal", "Discover", "Metal Albums"),
            ("discover library", "/discover/library?artist=%24ucide", "Discover", "Library Gaps"),
            ("collection", "/collection", "Collection", "Collection"),
            ("artist", "/artist/Matt%20Maltese", "Collection", "Matt Maltese"),
            (
                "album",
                "/album/_?artist=Mac%20Miller&album=The%20Divine%20Feminine",
                "Collection",
                "The Divine Feminine",
            ),
            ("settings", "/settings", "Settings", "General"),
        ]
        for label, path, expected_nav, expected_text in pages:
            response = await client.get(f"{base_url}{path}", follow_redirects=True)
            checks.append((response.status_code == 200, f"{label}: status 200"))
            body = response.text
            text = _text(body)
            checks.append(("Sign in to continue" not in text, f"{label}: stays authenticated"))
            checks.append(_assert_contains(label, text, expected_text))
            checks.append((_active_nav(body) == expected_nav, f"{label}: active nav is {expected_nav}"))
            checks.append((f"theme-{settings.app_theme}" in body, f"{label}: uses current theme"))

    passed = 0
    failed = 0
    for ok, message in checks:
        if ok:
            passed += 1
            print(f"[PASS] {message}")
        else:
            failed += 1
            print(f"[FAIL] {message}")
    print(f"Summary: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP smoke audit for Redwave UI structure.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    return asyncio.run(run(args.base_url.rstrip("/")))


if __name__ == "__main__":
    sys.exit(main())
