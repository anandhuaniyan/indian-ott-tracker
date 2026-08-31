"""SQLAlchemy ORM models."""

from app.models.enums import AvailabilityType
from app.models.genre import Genre, movie_genres, tv_show_genres
from app.models.language import Language, movie_languages, tv_show_languages
from app.models.movie import Movie
from app.models.movie_metadata import (
    AlternativeTitle,
    ExternalId,
    Keyword,
    MovieCredit,
    MovieImage,
    MovieTrailer,
    MovieKeyword,
    MovieProductionCompany,
    MovieProductionCountry,
    MovieRating,
    MovieReleaseDate,
    Person,
    ProductionCompany,
    ProductionCountry,
)
from app.models.movie_ott import MovieOtt
from app.models.ott_availability import OttAvailability
from app.models.ott_platform import OttPlatform
from app.models.tv_show import TVShow
from app.models.tv_show_ott import TVShowOtt
from app.models.operations import AdminAuditLog, BackfillRecord, DataQualityIssue, MovieComment, MovieRequest, NotificationLog, OperationState, OttEvidence, OttSourceRelease

__all__ = [
    "AvailabilityType",
    "Genre",
    "Language",
    "Movie",
    "AlternativeTitle",
    "ExternalId",
    "Keyword",
    "MovieCredit",
    "MovieImage",
    "MovieTrailer",
    "MovieKeyword",
    "MovieProductionCompany",
    "MovieProductionCountry",
    "MovieRating",
    "MovieReleaseDate",
    "Person",
    "ProductionCompany",
    "ProductionCountry",
    "MovieOtt",
    "OttAvailability",
    "OttPlatform",
    "TVShow",
    "TVShowOtt",
    "movie_genres",
    "movie_languages",
    "tv_show_genres",
    "tv_show_languages",
    "AdminAuditLog", "BackfillRecord", "MovieComment", "MovieRequest", "OttEvidence", "OttSourceRelease", "DataQualityIssue", "NotificationLog", "OperationState",
]
