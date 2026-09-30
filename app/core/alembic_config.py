"""Helpers for safely passing application settings into Alembic."""

from alembic.config import Config


def set_sqlalchemy_url(config: Config, database_url: str) -> None:
    """Set the database URL without ConfigParser treating URL escapes as interpolation."""

    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
