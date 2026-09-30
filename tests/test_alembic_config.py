"""Regression coverage for Alembic configuration handling."""

from alembic.config import Config

from app.core.alembic_config import set_sqlalchemy_url


def test_percent_encoded_database_url_survives_config_interpolation() -> None:
    config = Config("alembic.ini")
    database_url = (
        "postgresql+psycopg://ott_user:example%2Bpassword%25value%5E@"
        "postgres:5432/ott_tracker"
    )

    set_sqlalchemy_url(config, database_url)

    assert config.get_main_option("sqlalchemy.url") == database_url
