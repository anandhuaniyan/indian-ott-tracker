from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    APP_NAME: str = "Indian OTT Tracker"

    ENVIRONMENT: str = "development"

    DATABASE_URL: str

    REDIS_URL: str

    TMDB_API_KEY: str = ""

    SECRET_KEY: str


    class Config:
        env_file = ".env"


settings = Settings()