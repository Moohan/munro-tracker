from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MunroStream API"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://munro:munro@db:5432/munrostream"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    frontend_origin: str = "http://localhost:5173"
    strava_client_id: int = 0
    strava_client_secret: str = "replace-me"
    strava_redirect_uri: str | None = None
    strava_oauth_state_ttl_seconds: int = 600
    strava_sync_activity_limit: int = 25
    strava_webhook_secret: str = "replace-me"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
