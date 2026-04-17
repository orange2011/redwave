import os
import re
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from app.templates_config import templates
from app.config import settings

router = APIRouter()
ENV_PATH = Path(".env")


def _read_env() -> dict[str, str]:
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    return result


def _write_env(data: dict[str, str]):
    lines = [f"{k}={v}" for k, v in data.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n")


def _reload_settings(data: dict[str, str]):
    """Apply saved values to the live settings object."""
    mapping = {
        "RED_API_KEY": "red_api_key",
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
        "MUSIC_DIR": "music_dir",
        "NAVIDROME_URL": "navidrome_url",
        "NAVIDROME_USER": "navidrome_user",
        "NAVIDROME_PASS": "navidrome_pass",
        "LIDARR_URL": "lidarr_url",
        "LIDARR_API_KEY": "lidarr_api_key",
    }
    for env_key, attr in mapping.items():
        if env_key in data:
            object.__setattr__(settings, attr, data[env_key])


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    env = _read_env()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "env": env,
        "saved": request.query_params.get("saved"),
        "lastfm_connected": request.query_params.get("lastfm_connected"),
        "lastfm_error": request.query_params.get("lastfm_error"),
    })


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    form = await request.form()
    env = _read_env()
    fields = [
        "RED_API_KEY", "LASTFM_API_KEY", "LASTFM_SHARED_SECRET", "LASTFM_USERNAME",
        "NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASS",
        "LISTENBRAINZ_TOKEN", "DISCOGS_TOKEN",
        "QBT_HOST", "QBT_USERNAME", "QBT_PASSWORD", "QBT_CATEGORY",
        "MUSIC_DIR", "LIDARR_URL", "LIDARR_API_KEY",
        "LISTENBRAINZ_USERNAME",
    ]
    for f in fields:
        val = form.get(f, "").strip()
        if val:
            env[f] = val
        elif f in env and not val:
            env[f] = ""
    _write_env(env)
    _reload_settings(env)
    # Update scanner music dir (kept for cover art fallback)
    if env.get("MUSIC_DIR"):
        from app.services import scanner
        scanner.MUSIC_DIR = Path(env["MUSIC_DIR"])
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/settings?saved=1", status_code=303)


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
    import httpx
    try:
        r = httpx.get("https://redacted.sh/ajax.php", params={"action": "index"},
                      headers={"Authorization": settings.red_api_key},
                      timeout=10, follow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            return JSONResponse({"ok": False, "msg": "Invalid or expired API key (redirect)"})
        data = r.json()
        if data.get("status") == "success":
            u = data.get("response", {}).get("username", "")
            return JSONResponse({"ok": True, "msg": f"Connected as {u}"})
        return JSONResponse({"ok": False, "msg": data.get("error", "Unknown error")})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


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
    try:
        client = httpx.AsyncClient(base_url=settings.qbt_host, timeout=8)
        r = await client.post("/api/v2/auth/login", data={
            "username": settings.qbt_username,
            "password": settings.qbt_password,
        })
        if r.text == "Ok.":
            return JSONResponse({"ok": True, "msg": f"Connected to {settings.qbt_host}"})
        return JSONResponse({"ok": False, "msg": f"Login failed: {r.text}"})
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


@router.get("/api/settings/test/lidarr")
async def test_lidarr():
    import httpx
    url = settings.lidarr_url.rstrip("/")
    if not url:
        return JSONResponse({"ok": False, "msg": "No Lidarr URL configured"})
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{url}/api/v1/system/status",
                                 headers={"X-Api-Key": settings.lidarr_api_key})
            data = r.json()
            version = data.get("version", "")
            if version:
                return JSONResponse({"ok": True, "msg": f"Connected — Lidarr v{version}"})
            return JSONResponse({"ok": False, "msg": "Invalid API key"})
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)})


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
    p = Path(getattr(settings, "music_dir", "") or ".")
    if p.exists() and p.is_dir():
        count = sum(1 for _ in p.iterdir() if _.is_dir())
        return JSONResponse({"ok": True, "msg": f"Found — {count} entries"})
    return JSONResponse({"ok": False, "msg": f"Path not found: {p}"})


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
