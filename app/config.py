from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    red_api_key: str = ""
    lastfm_api_key: str = ""
    lastfm_shared_secret: str = ""
    lastfm_session_key: str = ""
    lastfm_username: str = ""
    listenbrainz_token: str = ""
    listenbrainz_username: str = ""
    discogs_token: str = ""
    qbt_host: str = "http://localhost:8080"
    qbt_username: str = "admin"
    qbt_password: str = "adminadmin"
    qbt_category: str = "music"
    database_url: str = "sqlite+aiosqlite:///./redwave.db"
    musicbrainz_user_agent: str = "Redwave/1.0 (redwave@localhost)"
    music_dir: str = ""
    navidrome_url: str = ""
    navidrome_user: str = ""
    navidrome_pass: str = ""
    lidarr_url: str = ""
    lidarr_api_key: str = ""
    app_username: str = "admin"
    app_password: str = "redwave"
    secret_key: str = "change-me-to-a-random-secret"

    class Config:
        env_file = ".env"


settings = Settings()
