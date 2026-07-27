"""Pydantic request and response schemas."""

from app.schemas.genre import GenreCreate, GenreRead
from app.schemas.language import LanguageCreate, LanguageRead
from app.schemas.movie import MovieCreate, MovieRead
from app.schemas.movie_ott import MovieOttCreate, MovieOttRead
from app.schemas.ott_platform import OttPlatformCreate, OttPlatformRead
from app.schemas.tv_show import TVShowCreate, TVShowRead
from app.schemas.tv_show_ott import TVShowOttCreate, TVShowOttRead

__all__ = [
    "GenreCreate",
    "GenreRead",
    "LanguageCreate",
    "LanguageRead",
    "MovieCreate",
    "MovieOttCreate",
    "MovieOttRead",
    "MovieRead",
    "OttPlatformCreate",
    "OttPlatformRead",
    "TVShowCreate",
    "TVShowOttCreate",
    "TVShowOttRead",
    "TVShowRead",
]
