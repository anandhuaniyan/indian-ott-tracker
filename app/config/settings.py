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
    SITE_URL: str = "http://localhost:5173"
    GOOGLE_ANALYTICS_ID: str = ""
    GOOGLE_SITE_VERIFICATION: str = ""
    ADSENSE_PUBLISHER_ID: str = ""
    ADMIN_NOTIFICATION_EMAIL: str = ""
    OTT_RESEARCH_PROVIDER: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
