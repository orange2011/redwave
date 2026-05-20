#!/usr/bin/env python
"""One-shot RED/OPS API key tester.

This script sends exactly one lightweight `action=index` request and never
prints the key. Prefer pasting the key at the prompt so it does not land in
shell history.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


TRACKERS = {
    "red": {
        "label": "RED",
        "url": "https://redacted.sh/ajax.php",
    },
    "ops": {
        "label": "OPS",
        "url": "https://orpheus.network/ajax.php",
    },
}
KEY_RE = re.compile(r"^[A-Za-z0-9._/\-+=]{20,}$")


def looks_rate_limited(text: str) -> bool:
    lowered = text.lower()
    return any(
        needle in lowered
        for needle in ("temporarily banned", "rate limit", "rate-limit", "too many requests")
    )


def response_kind(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return "json"
    if stripped.startswith("<!doctype") or stripped.startswith("<html") or "<body" in stripped[:500].lower():
        return "html"
    return "text"


def print_snippet(text: str) -> None:
    snippet = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if snippet:
        print(f"Response snippet: {snippet[:300]}")
    else:
        print("Response snippet: <empty>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test a RED or OPS API key with one request.")
    parser.add_argument("--tracker", choices=TRACKERS, default="red", help="Tracker to test. Default: red")
    parser.add_argument("--key", help="API key. Safer: omit this and paste it at the hidden prompt.")
    args = parser.parse_args()

    tracker = TRACKERS[args.tracker]
    label = tracker["label"]
    key = (args.key or getpass.getpass(f"{label} API key: ")).strip()
    if not key:
        print("No API key provided.")
        return 2
    if not KEY_RE.match(key):
        print("That does not look like an API key, so no request was sent.")
        print("Paste the raw key only, with no quotes, no 'Authorization:', and no 'Bearer'.")
        return 2

    query = urllib.parse.urlencode({"action": "index"})
    request = urllib.request.Request(
        f"{tracker['url']}?{query}",
        headers={
            "Authorization": key,
            "User-Agent": "RedwaveKeyTest/1.0",
        },
        method="GET",
    )

    print(f"{label}: testing {tracker['url']} with one request...")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"{label}: HTTP {exc.code}; response type looks like {response_kind(body)}")
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            print(f"Retry-After: {retry_after} seconds")
        if looks_rate_limited(body):
            print("Tracker says this IP is temporarily banned or rate-limited. Wait before testing again.")
        else:
            print_snippet(body)
        return 1
    except urllib.error.URLError as exc:
        print(f"{label}: connection failed: {exc.reason}")
        return 1
    except TimeoutError:
        print(f"{label}: request timed out.")
        return 1

    if looks_rate_limited(body):
        print(f"{label}: HTTP {status}; response type looks like {response_kind(body)}")
        print(f"{label}: tracker says this IP is temporarily banned or rate-limited. Wait before testing again.")
        print_snippet(body)
        return 1

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"{label}: HTTP {status}, but response was not JSON; response type looks like {response_kind(body)}.")
        print_snippet(body)
        return 1

    if data.get("status") == "success":
        username = data.get("response", {}).get("username") or "(username not returned)"
        print(f"{label}: OK. Connected as {username}.")
        return 0

    print(f"{label}: failed.")
    print(data.get("error") or data.get("message") or json.dumps(data, indent=2)[:500])
    return 1


if __name__ == "__main__":
    sys.exit(main())
