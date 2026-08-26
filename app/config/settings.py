from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Indian OTT Tracker"

    ENVIRONMENT: str = "development"

    DATABASE_URL: str

    REDIS_URL: str

    TMDB_API_KEY: str = ""

    SECRET_KEY: str

    LOG_LEVEL: str = "INFO"

    MEDIA_ROOT: str = "media"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
