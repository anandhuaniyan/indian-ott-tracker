from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Indian OTT Tracker"

    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "postgresql+psycopg://ott_user:ott_password@localhost:5433/ott_tracker"
    REDIS_URL: str = "redis://localhost:6380/0"

    TMDB_API_KEY: str = ""
    TMDB_ACCESS_TOKEN: str = ""
    IMDB_RATING_PROVIDER: str = ""
    IMDB_RATING_API_URL: str = ""
    IMDB_RATING_API_KEY: str = ""
    METADATA_BACKFILL_BATCH_SIZE: int = 100
    PERSON_BACKFILL_BATCH_SIZE: int = 100
    IMAGE_BACKFILL_BATCH_SIZE: int = 100
    IMDB_BACKFILL_BATCH_SIZE: int = 50
    IMDB_BACKFILL_DELAY_SECONDS: float = 0.1
    OTT_BACKFILL_BATCH_SIZE: int = 200
    BACKFILL_MAX_ATTEMPTS: int = 3
    ON_DEMAND_REPAIR_COOLDOWN_HOURS: int = 12

    SECRET_KEY: str = "change-me-in-production"

    LOG_LEVEL: str = "INFO"

    MEDIA_ROOT: str = "media"
    FRONTEND_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    ADMIN_API_KEY: str = ""
    ADMIN_SESSION_SECRET: str = ""
    ADMIN_PASSWORD_HASH: str = ""
    SITE_URL: str = "http://localhost:5173"
    SITE_TIMEZONE: str = "Asia/Kolkata"
    GOOGLE_ANALYTICS_ID: str = ""
    GOOGLE_SITE_VERIFICATION: str = ""
    ADSENSE_PUBLISHER_ID: str = ""
    SITE_CONTACT_EMAIL: str = ""
    ADMIN_NOTIFICATION_EMAIL: str = ""
    OTT_RESEARCH_PROVIDER: str = ""
    OTT_SEARCH_API_URL: str = ""
    OTT_SEARCH_API_KEY: str = ""
    GOOGLE_SEARCH_API_KEY: str = ""
    GOOGLE_SEARCH_ENGINE_ID: str = ""
    OTT_CONFIRMATION_THRESHOLD: float = 85.0
    OTT_RESEARCH_MIN_DAYS_AFTER_THEATRICAL_RELEASE: int = 7
    OTT_RESEARCH_HIGH_PRIORITY_DAYS: int = 90
    OTT_RESEARCH_MEDIUM_PRIORITY_DAYS: int = 180
    OTT_RESEARCH_AUTO_MAX_AGE_DAYS: int = 365
    OTT_DAILY_RESEARCH_MOVIE_LIMIT: int = 20
    TAVILY_API_KEY: str = ""
    TAVILY_MONTHLY_APP_BUDGET: int = 800
    TAVILY_MAX_QUERIES_PER_MOVIE: int = 2
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
