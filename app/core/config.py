from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="development")

    directus_url: str = Field(...)
    directus_token: str = Field(...)
    directus_timeout: float = Field(default=15.0)

    directus_users_collection: str = Field(default="users")
    directus_events_collection: str = Field(default="events")
    directus_wishes_collection: str = Field(default="wishes")
    directus_notifications_collection: str = Field(default="notifications")
    directus_users_telegram_field: str = Field(default="telegram_id")
    directus_users_locale_field: str = Field(default="locale")
    directus_events_owner_field: str = Field(default="owner")
    directus_events_date_field: str = Field(default="event_date")
    directus_events_title_field: str = Field(default="title")
    directus_wishes_owner_field: str = Field(default="owner")
    directus_wishes_title_field: str = Field(default="title")
    directus_wishes_created_field: str = Field(default="date_created")
    directus_notifications_user_field: str = Field(default="user")
    directus_notifications_event_field: str = Field(default="event")
    directus_notifications_days_field: str = Field(default="days_before")

    telegram_bot_token: str = Field(...)
    telegram_bot_username: str = Field(default="")

    google_client_id: str = Field(default="")

    jwt_secret: str = Field(...)
    jwt_algorithm: str = Field(default="HS256")
    jwt_expires_minutes: int = Field(default=60 * 24 * 7)

    parser_timeout_seconds: float = Field(default=10.0)
    parser_user_agent: str = Field(default="WishlleBot/1.0 (+https://wishlle.app)")

    notifier_cron_hour: int = Field(default=9)
    notifier_cron_minute: int = Field(default=0)
    notifier_timezone: str = Field(default="Europe/Kyiv")
    notifier_reminder_days: list[int] = Field(default_factory=lambda: [7, 3, 1, 0])
    notifier_wishes_enabled: bool = Field(default=True)
    notifier_wishes_lookback_hours: int = Field(default=24)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
