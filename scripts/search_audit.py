import argparse
import asyncio
import html
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.services.navidrome import get_newest_albums, get_random_songs

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _canon(value: str) -> str:
    value = html.unescape(value or "").lower().replace("&", " and ")
    return " ".join(_TOKEN_RE.findall(value))


def _html_text(markup: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", markup or "", flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _contains(text: str, *needles: str) -> bool:
    haystack = _canon(text)
    return all(_canon(needle) in haystack for needle in needles if needle)


async def _login(client: httpx.AsyncClient, base_url: str) -> None:
    user = settings.app_username
    password = settings.app_password
    if not user or not password:
        return
    await client.post(
        f"{base_url}/login",
        data={"username": user, "password": password},
        follow_redirects=True,
    )


async def _search(client: httpx.AsyncClient, base_url: str, query: str) -> str:
    response = await client.get(f"{base_url}/search", params={"q": query}, follow_redirects=True)
    response.raise_for_status()
    return _html_text(response.text)


async def _check_album(client: httpx.AsyncClient, base_url: str, album: dict) -> tuple[bool, str]:
    query = f"{album.get('artist', '')} {album.get('album', '')}".strip()
    text = await _search(client, base_url, query)
    ok = _contains(text, album.get("artist", ""), album.get("album", ""))
    label = f"album | {album.get('artist', '')} - {album.get('album', '')}"
    return ok, f"{label} | query={query}"


async def _check_song(client: httpx.AsyncClient, base_url: str, song: dict) -> tuple[bool, str]:
    query = f"{song.get('artist', '')} {song.get('track', '')}".strip()
    text = await _search(client, base_url, query)
    ok = _contains(text, song.get("artist", ""), song.get("track", ""))
    label = f"song  | {song.get('artist', '')} - {song.get('track', '')}"
    return ok, f"{label} | query={query}"


async def run(base_url: str, limit: int) -> int:
    albums, songs = await asyncio.gather(get_newest_albums(limit), get_random_songs(limit))
    checks = []
    async with httpx.AsyncClient(timeout=30) as client:
        await _login(client, base_url)
        for album in albums:
            checks.append(await _check_album(client, base_url, album))
        for song in songs:
            checks.append(await _check_song(client, base_url, song))

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
    parser = argparse.ArgumentParser(description="Audit Redwave search against Navidrome library samples.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    return asyncio.run(run(args.base_url.rstrip("/"), max(1, args.limit)))


if __name__ == "__main__":
    sys.exit(main())
