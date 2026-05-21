import os
import re
import json
import time
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from app.templates_config import templates
from app.config import settings

router = APIRouter()
ENV_PATH = Path(".env")
TRACKER_TEST_COOLDOWN_SECONDS = 60 * 60
_tracker_test_backoff: dict[str, float] = {}

SETTINGS_ENV_TO_ATTR = {
    "RED_API_KEY": "red_api_key",
    "RED_USE_FREELEECH_TOKEN": "red_use_freeleech_token",
    "RED_QUALITY_PROFILE": "red_quality_profile",
    "RED_MEDIA_PREFERENCE": "red_media_preference",
    "RED_MEDIA_SCORE_CD": "red_media_score_cd",
    "RED_MEDIA_SCORE_WEB": "red_media_score_web",
    "RED_MEDIA_SCORE_VINYL": "red_media_score_vinyl",
    "RED_MEDIA_SCORE_CASSETTE": "red_media_score_cassette",
    "RED_MEDIA_SCORE_SACD": "red_media_score_sacd",
    "RED_MEDIA_SCORE_BLU_RAY": "red_media_score_blu_ray",
    "RED_MEDIA_SCORE_DVD": "red_media_score_dvd",
    "RED_MEDIA_SCORE_SOUNDBOARD": "red_media_score_soundboard",
    "OPS_API_KEY": "ops_api_key",
    "LASTFM_API_KEY": "lastfm_api_key",
    "LASTFM_SHARED_SECRET": "lastfm_shared_secret",
    "LASTFM_SESSION_KEY": "lastfm_session_key",
    "LASTFM_USERNAME": "lastfm_username",
    "LISTENBRAINZ_TOKEN": "listenbrainz_token",
    "LISTENBRAINZ_USERNAME": "listenbrainz_username",
    "DISCOGS_TOKEN": "discogs_token",
    "QBT_HOST": "qbt_host",
    "QBT_USERNAME": "qbt_username",
    "QBT_PASSWORD": "qbt_password",
    "QBT_CATEGORY": "qbt_category",
    "QBT_RED_TAG": "qbt_red_tag",
    "QBT_OPS_TAG": "qbt_ops_tag",
    "OPS_CROSS_SEED": "ops_cross_seed",
    "MUSIC_DIR": "music_dir",
    "NAVIDROME_URL": "navidrome_url",
    "NAVIDROME_USER": "navidrome_user",
    "NAVIDROME_PASS": "navidrome_pass",
    "APP_THEME": "app_theme",
}

SETTINGS_FORM_FIELDS = [
    "RED_API_KEY", "RED_USE_FREELEECH_TOKEN", "RED_QUALITY_PROFILE",
    "OPS_API_KEY",
    "RED_MEDIA_SCORE_CD", "RED_MEDIA_SCORE_WEB", "RED_MEDIA_SCORE_VINYL", "RED_MEDIA_SCORE_CASSETTE",
    "RED_MEDIA_SCORE_SACD", "RED_MEDIA_SCORE_BLU_RAY", "RED_MEDIA_SCORE_DVD", "RED_MEDIA_SCORE_SOUNDBOARD",
    "LASTFM_API_KEY", "LASTFM_SHARED_SECRET", "LASTFM_USERNAME",
    "NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASS",
    "LISTENBRAINZ_TOKEN", "DISCOGS_TOKEN",
    "QBT_HOST", "QBT_USERNAME", "QBT_PASSWORD", "QBT_CATEGORY",
    "QBT_RED_TAG", "QBT_OPS_TAG", "OPS_CROSS_SEED",
    "MUSIC_DIR",
    "LISTENBRAINZ_USERNAME",
    "APP_THEME",
]


def _read_env() -> dict[str, str]:
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    return result


def _env_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, _ = stripped.partition("=")
    if key.startswith("export "):
        key = key[7:]
    key = key.strip()
    return key or None


def _backup_env():
    if not ENV_PATH.exists():
        return
    backup_path = ENV_PATH.parent / f"{ENV_PATH.name}.bak"
    backup_path.write_text(ENV_PATH.read_text())


def _write_env(data: dict[str, str], remove_keys: set[str] | None = None):
    """Write .env without destroying comments, blank lines, or unknown local keys."""
    remove_keys = remove_keys or set()
    seen: set[str] = set()
    output: list[str] = []
    original_lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []

    _backup_env()

    for line in original_lines:
        key = _env_key(line)
        if key is None:
            output.append(line)
            continue
        if key in remove_keys:
            continue
        if key in data:
            output.append(f"{key}={data[key]}")
            seen.add(key)
        else:
            output.append(line)

    for key, value in data.items():
        if key not in seen and key not in remove_keys:
            output.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(output).rstrip() + "\n")


def _reload_settings(data: dict[str, str]):
    """Apply saved values to the live settings object."""
    for env_key, attr in SETTINGS_ENV_TO_ATTR.items():
        if env_key in data:
            object.__setattr__(settings, attr, data[env_key])


def _env_with_live_settings_defaults() -> dict[str, str]:
    env = _read_env()
    for env_key, attr in SETTINGS_ENV_TO_ATTR.items():
        value = getattr(settings, attr, "")
        if value not in (None, ""):
            env.setdefault(env_key, str(value))
    return env


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    env = _env_with_live_settings_defaults()
    from app.services.redacted import media_score_options
    media_scores = media_score_options()
    for item in media_scores:
        env.setdefault(item["env_key"], str(getattr(settings, item["attr"], item["default"])))
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "env": env,
        "media_scores": media_scores,
        "saved": request.query_params.get("saved"),
        "lastfm_connected": request.query_params.get("lastfm_connected"),
        "lastfm_error": request.query_params.get("lastfm_error"),
    })


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    form = await request.form()
    env = _read_env()
    removed_keys = {"LIDARR_URL", "LIDARR_API_KEY"}
    for removed in removed_keys:
        env.pop(removed, None)
    for f in SETTINGS_FORM_FIELDS:
        val = form.get(f, "").strip()
        if f == "APP_THEME" and val not in {"redwave", "black", "light"}:
            val = "redwave"
        if f == "RED_QUALITY_PROFILE":
            from app.services.redacted import normalize_quality_profile
            val = normalize_quality_profile(val)
        if f == "OPS_CROSS_SEED" and val not in {"0", "1"}:
            val = "0"
        if f.startswith("RED_MEDIA_SCORE_"):
            try:
                val = str(max(-100000, min(100000, int(val or "0"))))
            except ValueError:
                val = "0"
        if val:
            env[f] = val
        elif f in env and not val:
            env[f] = ""
    _write_env(env, remove_keys=removed_keys)
    _reload_settings(env)
    # Update scanner music dir (kept for cover art fallback)
    if env.get("MUSIC_DIR"):
        from app.services import scanner
        scanner.MUSIC_DIR = Path(env["MUSIC_DIR"])
    save_checks = await _run_configured_save_checks(env)
    save_failed = any(check.get("ok") is False for check in save_checks)
    from app.services.redacted import media_score_options
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "env": env,
        "media_scores": media_score_options(),
        "saved": "1",
        "save_checks": save_checks,
        "save_failed": save_failed,
        "lastfm_connected": request.query_params.get("lastfm_connected"),
        "lastfm_error": request.query_params.get("lastfm_error"),
    })


# ── Test endpoints ──────────────────────────────────────────────────────────

@router.get("/api/settings/test/lastfm")
async def test_lastfm():
    import httpx
    try:
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "user.getinfo",
            "user": settings.lastfm_username,
            "api_key": settings.lastfm_api_key,
            "format": "json",
        }, timeout=8)
        data = r.json()
        if data.get("user"):
            return JSONResponse({"ok": True, "msg": f"Connected as {data['user']['name']} ({data['user']['playcount']} scrobbles)"})
        return JSONResponse({"ok": False, "msg": data.get("message", "Invalid API key or username")})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


@router.get("/api/settings/test/lastfm-secret")
async def test_lastfm_secret():
    import hashlib
    import httpx
    secret = settings.lastfm_shared_secret
    api_key = settings.lastfm_api_key
    if not secret:
        return JSONResponse({"ok": False, "msg": "No shared secret configured"})
    if not api_key:
        return JSONResponse({"ok": False, "msg": "No API key configured"})
    try:
        sig_params = {"api_key": api_key, "method": "auth.getToken"}
        sig_str = "".join(f"{k}{v}" for k, v in sorted(sig_params.items())) + secret
        api_sig = hashlib.md5(sig_str.encode("utf-8")).hexdigest()
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "auth.getToken",
            "api_key": api_key,
            "api_sig": api_sig,
            "format": "json",
        }, timeout=8)
        data = r.json()
        if data.get("token"):
            return JSONResponse({"ok": True, "msg": "Shared secret is valid — auth token obtained"})
        return JSONResponse({"ok": False, "msg": data.get("message", "Invalid secret or API key")})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


@router.get("/api/settings/test/red")
async def test_red():
    return await _test_gazelle_index(
        key="red",
        label="RED",
        url="https://redacted.sh/ajax.php",
        api_key=settings.red_api_key,
    )


@router.get("/api/settings/test/ops")
async def test_ops():
    return await _test_gazelle_index(
        key="ops",
        label="OPS",
        url="https://orpheus.network/ajax.php",
        api_key=settings.ops_api_key,
    )


async def _test_gazelle_index(key: str, label: str, url: str, api_key: str) -> JSONResponse:
    import httpx
    if not api_key.strip():
        return JSONResponse({"ok": False, "msg": f"No {label} API key configured"})
    cooldown_until = _tracker_test_backoff.get(key, 0)
    if cooldown_until > time.time():
        minutes = max(1, round((cooldown_until - time.time()) / 60))
        return JSONResponse({
            "ok": False,
            "msg": f"{label} recently reported a temporary ban/rate limit. Redwave will not retest for about {minutes} minutes.",
        })
    try:
        r = httpx.get(url, params={"action": "index"},
                      headers={"Authorization": api_key},
                      timeout=10, follow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            return JSONResponse({"ok": False, "msg": f"{label} API key invalid or expired (redirect)"})
        body = r.text or ""
        if _looks_like_tracker_rate_limit(body):
            _tracker_test_backoff[key] = time.time() + TRACKER_TEST_COOLDOWN_SECONDS
            return JSONResponse({
                "ok": False,
                "msg": f"{label} says your IP is temporarily banned/rate-limited. Stop testing/searching it for a while.",
            })
        data = r.json()
        if data.get("status") == "success":
            u = data.get("response", {}).get("username", "")
            return JSONResponse({"ok": True, "msg": f"Connected as {u}"} if u else {"ok": True, "msg": f"{label} connected"})
        error = data.get("error", "Unknown error")
        if _looks_like_tracker_rate_limit(error):
            _tracker_test_backoff[key] = time.time() + TRACKER_TEST_COOLDOWN_SECONDS
            return JSONResponse({
                "ok": False,
                "msg": f"{label} says your IP is temporarily banned/rate-limited. Stop testing/searching it for a while.",
            })
        return JSONResponse({"ok": False, "msg": data.get("error", "Unknown error")})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


def _looks_like_tracker_rate_limit(value: str) -> bool:
    value = (value or "").lower()
    return any(part in value for part in (
        "temporarily banned",
        "rate limit",
        "rate-limit",
        "too many requests",
    ))


@router.get("/api/settings/test/listenbrainz")
async def test_listenbrainz():
    import httpx
    try:
        r = httpx.get("https://api.listenbrainz.org/1/validate-token",
                      headers={"Authorization": f"Token {settings.listenbrainz_token}"},
                      timeout=8)
        data = r.json()
        if data.get("valid"):
            return JSONResponse({"ok": True, "msg": f"Connected as {data.get('user_name', '')}"})
        return JSONResponse({"ok": False, "msg": data.get("message", "Invalid token")})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


@router.get("/api/settings/test/discogs")
async def test_discogs():
    import httpx
    try:
        r = httpx.get("https://api.discogs.com/oauth/identity",
                      headers={"Authorization": f"Discogs token={settings.discogs_token}",
                               "User-Agent": "Redwave/1.0"},
                      timeout=8)
        data = r.json()
        if data.get("username"):
            return JSONResponse({"ok": True, "msg": f"Connected as {data['username']}"})
        return JSONResponse({"ok": False, "msg": data.get("message", "Invalid token")})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


@router.get("/api/settings/test/qbittorrent")
async def test_qbittorrent():
    import httpx
    from app.services.qbittorrent import QBittorrentClient, qbt_base_url, _qbt_headers
    try:
        base_url = qbt_base_url()
        async with httpx.AsyncClient(timeout=8, headers=_qbt_headers(base_url)) as client:
            await QBittorrentClient().login(client, base_url)
            r = await client.get(f"{base_url}/api/v2/app/version")
            version = r.text.strip()
        msg = f"Connected to {base_url}"
        if version:
            msg += f" ({version})"
        return JSONResponse({"ok": True, "msg": msg})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})



@router.get("/api/debug/red-top")
async def debug_red_top():
    import httpx
    try:
        r = await httpx.AsyncClient(timeout=10).get(
            "https://redacted.sh/ajax.php",
            params={"action": "top10", "type": "torrents", "limit": 3, "way": "week"},
            headers={"Authorization": settings.red_api_key},
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/settings/test/navidrome")
async def test_navidrome():
    import httpx
    url = settings.navidrome_url.rstrip("/")
    if not url:
        return JSONResponse({"ok": False, "msg": "No Navidrome URL configured"})
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{url}/rest/ping", params={
                "u": settings.navidrome_user,
                "p": settings.navidrome_pass,
                "v": "1.16.0",
                "c": "Redwave",
                "f": "json",
            })
            data = r.json()
            status = data.get("subsonic-response", {}).get("status")
            if status == "ok":
                return JSONResponse({"ok": True, "msg": f"Connected to Navidrome"})
            err = data.get("subsonic-response", {}).get("error", {}).get("message", "Auth failed")
            return JSONResponse({"ok": False, "msg": err})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


@router.get("/api/settings/test/musicdir")
async def test_musicdir():
    raw_path = (getattr(settings, "music_dir", "") or "").strip()
    if not raw_path:
        return JSONResponse({"ok": False, "msg": "No Music Directory configured"})
    if re.match(r"^/.+:/[^:]+(:ro|:rw)?$", raw_path):
        _, container_path, *_ = raw_path.split(":")
        return JSONResponse({
            "ok": False,
            "msg": f"This looks like a Docker volume mapping. Put it in docker-compose, then set Music Directory to {container_path}",
        })
    p = Path(raw_path)
    if p.exists() and p.is_dir():
        count = sum(1 for _ in p.iterdir() if _.is_dir())
        return JSONResponse({"ok": True, "msg": f"Found — {count} entries"})
    return JSONResponse({"ok": False, "msg": f"Path not found: {p}"})


async def _response_json(response: JSONResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


async def _run_configured_save_checks(env: dict[str, str]) -> list[dict]:
    checks = [
        ("Last.fm", ("LASTFM_API_KEY", "LASTFM_USERNAME"), test_lastfm),
        ("Last.fm account link", ("LASTFM_SHARED_SECRET",), test_lastfm_secret),
        ("Navidrome", ("NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASS"), test_navidrome),
        ("Music folder", ("MUSIC_DIR",), test_musicdir),
        ("ListenBrainz", ("LISTENBRAINZ_TOKEN",), test_listenbrainz),
        ("Discogs", ("DISCOGS_TOKEN",), test_discogs),
        ("qBittorrent", ("QBT_HOST", "QBT_USERNAME", "QBT_PASSWORD"), test_qbittorrent),
    ]
    results = []
    for label, key in (("RED", "RED_API_KEY"), ("OPS", "OPS_API_KEY")):
        if (env.get(key) or "").strip():
            results.append({
                "label": label,
                "ok": None,
                "msg": "Configured. Skipped on Save to avoid tracker rate limits; use Test when you need to verify it.",
            })
        else:
            results.append({"label": label, "ok": None, "msg": "Not configured"})
    for label, keys, fn in checks:
        if not any((env.get(key) or "").strip() for key in keys):
            results.append({"label": label, "ok": None, "msg": "Not configured"})
            continue
        data = await _response_json(await fn())
        results.append({"label": label, "ok": bool(data.get("ok")), "msg": data.get("msg", "")})
    return results


# ── Last.fm OAuth ────────────────────────────────────────────────────────────

# Temporary store for the pending auth token (single-user local app)
_pending_lastfm_token: str = ""


def _lastfm_sig(params: dict, secret: str) -> str:
    import hashlib
    sig_str = "".join(f"{k}{v}" for k, v in sorted(params.items())) + secret
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest()


@router.get("/auth/lastfm")
async def lastfm_auth_redirect():
    global _pending_lastfm_token
    import httpx
    import urllib.parse
    from fastapi.responses import RedirectResponse

    api_key = settings.lastfm_api_key.strip()
    secret = settings.lastfm_shared_secret.strip()

    # Step 1: request a token from Last.fm (desktop flow)
    try:
        sig = _lastfm_sig({"api_key": api_key, "method": "auth.getToken"}, secret)
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "auth.getToken",
            "api_key": api_key,
            "api_sig": sig,
            "format": "json",
        }, timeout=8)
        data = r.json()
        token = data.get("token", "")
        if not token:
            err = urllib.parse.quote(data.get("message", "failed to get token"))
            return RedirectResponse(f"/settings?lastfm_error={err}")
    except Exception as e:
        return RedirectResponse(f"/settings?lastfm_error={urllib.parse.quote(str(e))}")

    # Step 2: store token, redirect user to Last.fm auth page
    _pending_lastfm_token = token
    auth_url = f"https://www.last.fm/api/auth/?api_key={api_key}&token={token}"
    return RedirectResponse(auth_url)



@router.get("/auth/lastfm/callback")
async def lastfm_auth_callback():
    global _pending_lastfm_token
    import httpx
    import urllib.parse
    from fastapi.responses import RedirectResponse

    token = _pending_lastfm_token
    if not token:
        return RedirectResponse("/settings?lastfm_error=no+pending+token")

    api_key = settings.lastfm_api_key.strip()
    secret = settings.lastfm_shared_secret.strip()

    sig = _lastfm_sig({"api_key": api_key, "method": "auth.getSession", "token": token}, secret)
    try:
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "auth.getSession",
            "api_key": api_key,
            "token": token,
            "api_sig": sig,
            "format": "json",
        }, timeout=8)
        data = r.json()
        session = data.get("session", {})
        session_key = session.get("key", "")
        username = session.get("name", "")
        if session_key:
            _pending_lastfm_token = ""
            env = _read_env()
            env["LASTFM_SESSION_KEY"] = session_key
            _write_env(env)
            object.__setattr__(settings, "lastfm_session_key", session_key)
            return RedirectResponse(f"/settings?lastfm_connected={urllib.parse.quote(username)}")
        err_msg = data.get("message", "no session key returned")
        return RedirectResponse(f"/settings?lastfm_error={urllib.parse.quote(err_msg)}")
    except Exception as e:
        return RedirectResponse(f"/settings?lastfm_error={urllib.parse.quote(str(e))}")


@router.post("/auth/lastfm/disconnect")
async def lastfm_disconnect():
    from fastapi.responses import RedirectResponse
    env = _read_env()
    env["LASTFM_SESSION_KEY"] = ""
    _write_env(env)
    object.__setattr__(settings, "lastfm_session_key", "")
    return RedirectResponse("/settings?saved=1", status_code=303)
