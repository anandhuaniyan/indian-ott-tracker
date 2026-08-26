from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Indian OTT Tracker"

    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "postgresql+psycopg://ott_user:ott_password@localhost:5433/ott_tracker"
    REDIS_URL: str = "redis://localhost:6380/0"

    TMDB_API_KEY: str = ""

    SECRET_KEY: str = "change-me-in-production"

    LOG_LEVEL: str = "INFO"

    MEDIA_ROOT: str = "media"
    FRONTEND_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    ADMIN_API_KEY: str = ""
    ADMIN_SESSION_SECRET: str = ""
    ADMIN_PASSWORD_HASH: str = ""
    SITE_URL: str = "http://localhost:5173"
    GOOGLE_ANALYTICS_ID: str = ""
    GOOGLE_SITE_VERIFICATION: str = ""
    ADSENSE_PUBLISHER_ID: str = ""
    SITE_CONTACT_EMAIL: str = ""
    ADMIN_NOTIFICATION_EMAIL: str = ""
    OTT_RESEARCH_PROVIDER: str = ""
    OTT_SEARCH_API_URL: str = ""
    OTT_SEARCH_API_KEY: str = ""
    OTT_CONFIRMATION_THRESHOLD: float = 85.0
    DISCORD_WEBHOOK_URL: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
