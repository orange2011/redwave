from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    red_api_key: str = ""
    red_use_freeleech_token: str = "never"  # never | preferred | required
    red_quality_profile: str = "flac_any"
    red_media_preference: str = "cd"
    red_media_score_cd: int = 100
    red_media_score_web: int = 50
    red_media_score_vinyl: int = -10000
    red_media_score_cassette: int = 0
    red_media_score_sacd: int = 90
    red_media_score_blu_ray: int = 80
    red_media_score_dvd: int = 70
    red_media_score_soundboard: int = 20
    ops_api_key: str = ""
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
    qbt_red_tag: str = "RED"
    qbt_ops_tag: str = "OPS"
    ops_cross_seed: str = "0"
    database_url: str = "sqlite+aiosqlite:///./redwave.db"
    musicbrainz_user_agent: str = "Redwave/1.0 (redwave@localhost)"
    music_dir: str = ""
    navidrome_url: str = ""
    navidrome_user: str = ""
    navidrome_pass: str = ""
    app_theme: str = "redwave"
    app_username: str = "admin"
    app_password: str = "redwave"
    secret_key: str = "change-me-to-a-random-secret"


settings = Settings()
