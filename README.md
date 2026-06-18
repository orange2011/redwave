# Redwave

Self-hosted music discovery and tracker search for RED and OPS.

## Tracker Settings

Configure trackers from the Settings page or with environment variables:

```env
TRACKER_MODE=both
PRIMARY_TRACKER=red
RED_API_KEY=
OPS_API_KEY=
RED_USE_FREELEECH_TOKEN=never
OPS_USE_FREELEECH_TOKEN=never
```

`TRACKER_MODE` accepts `both`, `red`, or `ops`. Single-tracker modes do not
search, run tracklist fallback, or cross-seed through the disabled tracker.

Each token policy accepts `never`, `preferred`, or `required`. OPS token use
uses the documented `usetoken=1` download argument. The OPS bonus shop is not
part of the API-token interface and must be used manually.

`OPS_CROSS_SEED=1` enables a guarded RED/OPS cross-seed check in either
direction. If you grab RED, Redwave looks for a safe OPS match after completion;
if you grab OPS, it looks for a safe RED match. It only adds the second torrent
when the torrent payload is exact or can be safely mapped to the completed files.

Home-page discovery shelves are maintained as background snapshots. Last.fm top
albums refresh hourly, recommendations refresh every 2 hours, primary tracker
top albums refresh every 24 hours, ListenBrainz weekly playlists refresh on the
weekly schedule, and Navidrome collection snapshots refresh every 10 minutes.

## Docker

```bash
docker compose up -d --build redwave
```

The app listens on port `8000` by default. Keep `.env`, database files, logs,
and local media paths out of Git; the included ignore files already exclude
them.
