# Redwave

Redwave is a local music discovery and release-finding web UI for a Navidrome library. It combines your own collection with Last.fm, ListenBrainz, MusicBrainz, RED, OPS, and qBittorrent so you can search, discover, and grab music without bouncing between several tools.

The app is built with FastAPI, Jinja templates, SQLite, and a small amount of vanilla JavaScript.

## Features

- Spotify-style search layout with best match, songs, artists, albums, and library results.
- Fuzzy library search, including punctuation and symbol-heavy artist names.
- URL import/search support for common music links, including RED torrent URLs.
- RED release search with quality and media preferences.
- Optional OPS fallback when RED has no matching release.
- Optional OPS cross-seed matching after a RED download completes.
- Freeleech token preferences for RED: never, preferred, or required.
- Discovery pages for genres, recommendations, top albums, and library gaps.
- Collection view with album and artist counts, artist drilldowns, and wider album grids.
- Navidrome cache refresh, automatic home refresh, and manual refresh controls.
- Theme support: Redwave textured, RED-inspired black, and RED-inspired light.
- qBittorrent handoff with local request tracking.

## Tracker Behavior

The release finder searches RED first. If RED returns matching releases, Redwave shows only RED results and ranks them using your quality and media settings. If RED has no usable result, Redwave falls back to OPS and labels those releases clearly in the picker.

RED freeleech tokens are only used for RED downloads. OPS does not receive RED token parameters.

When OPS cross-seeding is enabled, Redwave grabs the selected RED torrent first. After qBittorrent reports that RED torrent as complete/seeding, Redwave searches OPS for an exact-size matching torrent and adds that OPS torrent too. Use separate qBittorrent tags for RED and OPS so the two trackers stay visually distinct.

## Quality Preferences

Settings let you tune how releases are ranked instead of hiding everything else. For example, you can prefer lossless FLAC and CD sources while still allowing WEB, SACD, Blu-Ray, DVD, Soundboard, Vinyl, or Cassette lower in the list.

The default profile is lossless-friendly:

- Preferred quality: FLAC
- Preferred media: CD
- CD, SACD, Blu-Ray, and DVD score above WEB
- Vinyl is strongly penalized by default, but you can change that

## Setup

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` or use the Settings page in the app. Do not commit `.env`.

Start the app:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

The default local login is configured by `APP_USERNAME` and `APP_PASSWORD`.

## Docker

```powershell
docker compose up --build
```

The compose file runs Redwave on port `8000` and mounts `redwave.db` from the project directory.

## Configuration

Important environment values:

- `APP_USERNAME`, `APP_PASSWORD`, `SECRET_KEY`
- `APP_THEME`
- `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASS`
- `LASTFM_API_KEY`, `LASTFM_SHARED_SECRET`, `LASTFM_USERNAME`
- `LISTENBRAINZ_TOKEN`, `LISTENBRAINZ_USERNAME`
- `RED_API_KEY`
- `OPS_API_KEY`
- `RED_USE_FREELEECH_TOKEN`
- `RED_QUALITY_PROFILE`
- `RED_MEDIA_PREFERENCE`
- `RED_MEDIA_SCORE_CD`, `RED_MEDIA_SCORE_WEB`, `RED_MEDIA_SCORE_VINYL`, `RED_MEDIA_SCORE_CASSETTE`
- `RED_MEDIA_SCORE_SACD`, `RED_MEDIA_SCORE_BLU_RAY`, `RED_MEDIA_SCORE_DVD`, `RED_MEDIA_SCORE_SOUNDBOARD`
- `QBT_HOST`, `QBT_USERNAME`, `QBT_PASSWORD`, `QBT_CATEGORY`
- `QBT_RED_TAG`, `QBT_OPS_TAG`, `OPS_CROSS_SEED`
- `MUSIC_DIR`

Secrets belong in `.env` or the local Settings page. Keep API keys out of commits, screenshots, logs, and issue reports.

## Tests

Run the unit tests:

```powershell
python -m unittest
```

Check Python syntax:

```powershell
python -m compileall app tests scripts
```

Run the HTTP UI audit against a running local server:

```powershell
python scripts\ui_http_audit.py --base-url http://127.0.0.1:8000
```

Run the Navidrome-backed search audit:

```powershell
python scripts\search_audit.py --base-url http://127.0.0.1:8000 --limit 5
```

The search audit samples real Navidrome albums and songs, searches Redwave, and prints a pass/fail summary without printing API keys.

## Development Notes

- Keep changes local until you intentionally commit and push.
- Prefer editing `.env.example` when adding new settings, but never add real credentials there.
- The local SQLite database and MusicBrainz dump are ignored by git.
- After UI changes, test desktop and phone-width layouts before publishing.
- After tracker changes, test RED behavior first, then OPS fallback with a non-sensitive local token.
