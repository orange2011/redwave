import httpx
from app.config import settings


class ListenBrainzClient:
    BASE_URL = "https://api.listenbrainz.org/1"

    def __init__(self):
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Token {settings.listenbrainz_token}"},
            timeout=10.0,
        )

    async def get_fresh_releases(self, limit: int = 12) -> list[dict]:
        try:
            r = await self._client.get(f"{self.BASE_URL}/explore/fresh-releases/", params={
                "sort": "release_date",
                "past_days": 3,
                "future_days": 3,
            })
            r.raise_for_status()
            data = r.json()
            releases = data.get("payload", {}).get("releases", [])[:limit]
            return [
                {
                    "artist": r.get("artist_credit_name", ""),
                    "album": r.get("release_name", ""),
                    "mb_id": r.get("release_mbid", ""),
                    "cover_url": f"https://coverartarchive.org/release/{r.get('release_mbid', '')}/front-250" if r.get("release_mbid") else None,
                    "release_date": r.get("release_date", ""),
                }
                for r in releases
            ]
        except Exception:
            return []

    async def get_weekly_playlists(self) -> list[dict]:
        """Fetch the weekly recommendation playlists ListenBrainz generates every Monday."""
        try:
            lb_user = settings.listenbrainz_username or settings.lastfm_username
            r = await self._client.get(
                f"{self.BASE_URL}/user/{lb_user}/playlists/createdfor"
            )
            r.raise_for_status()
            playlists = r.json().get("playlists", [])

            # Only keep weekly playlists, deduplicate by type (most recent first)
            seen_types: set = set()
            weekly = []
            for pl in playlists:
                p = pl.get("playlist", {})
                title = p.get("title", "")
                tl = title.lower()
                if "weekly jams" in tl:
                    kind = "jams"
                elif "exploration" in tl:
                    kind = "exploration"
                else:
                    continue
                if kind in seen_types:
                    continue
                seen_types.add(kind)
                mbid = p.get("identifier", "").split("/")[-1]
                weekly.append({"title": title, "mbid": mbid})

            # Fetch full track data for each playlist
            result = []
            for pl in weekly:
                try:
                    pr = await self._client.get(f"{self.BASE_URL}/playlist/{pl['mbid']}")
                    pr.raise_for_status()
                    full = pr.json().get("playlist", {})
                    tracks = []
                    for t in full.get("track", [])[:50]:
                        ext = t.get("extension", {}).get("https://musicbrainz.org/doc/jspf#track", {})
                        release_id = (ext.get("release_identifier") or "").split("/")[-1]
                        dur_ms = t.get("duration") or 0
                        dur_s = int(dur_ms) // 1000
                        duration = f"{dur_s//60}:{dur_s%60:02d}" if dur_s else ""
                        # album title may come from annotation field
                        album = t.get("album", "") or ext.get("additional_metadata", {}).get("release_name", "")
                        tracks.append({
                            "title": t.get("title", ""),
                            "artist": t.get("creator", ""),
                            "album": album,
                            "duration": duration,
                            "release_mbid": release_id,
                        })
                    # Clean title: strip "for Username, week of YYYY-MM-DD"
                    clean_title = pl["title"].split(" for ")[0]
                    result.append({"title": clean_title, "mbid": pl["mbid"], "tracks": tracks})
                except Exception:
                    continue
            return result
        except Exception:
            return []


lb_client = ListenBrainzClient()
