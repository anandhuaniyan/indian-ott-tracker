"""SQLAlchemy ORM models."""

from app.models.enums import AvailabilityType
from app.models.genre import Genre, movie_genres, tv_show_genres
from app.models.language import Language, movie_languages, tv_show_languages
from app.models.movie import Movie
from app.models.movie_ott import MovieOtt
from app.models.ott_platform import OttPlatform
from app.models.tv_show import TVShow
from app.models.tv_show_ott import TVShowOtt

__all__ = [
    "AvailabilityType",
    "Genre",
    "Language",
    "Movie",
    "MovieOtt",
    "OttPlatform",
    "TVShow",
    "TVShowOtt",
    "movie_genres",
    "movie_languages",
    "tv_show_genres",
    "tv_show_languages",
]
