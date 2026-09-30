"""One normalized representation shared by movie-request email and Discord."""

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.movie import Movie
from app.models.operations import MovieRequest
from app.services.email_layout import canonical_poster_url
from app.services.release_status import best_canonical_ott


@dataclass(frozen=True)
class RequestNotificationData:
    request_id: str
    movie_id: int | None
    tmdb_id: int | None
    imdb_id: str | None
    title: str
    original_title: str | None
    poster_url: str | None
    release_date: str | None
    year: int | None
    language: str | None
    status: str
    ott_platform: str | None
    ott_release_date: str | None
    movie_url: str | None
    already_in_library: bool

    def as_dict(self) -> dict:
        return asdict(self)


def request_notification_data(db: Session, item: MovieRequest) -> RequestNotificationData:
    movie = db.get(Movie, item.local_movie_id) if item.local_movie_id else None
    ott = best_canonical_ott(movie) if movie else None
    meaningful_ott = bool(ott and ott.provider and (ott.confidence or 0) > 0)
    title = item.verified_title or item.movie_name
    release_date = item.verified_release_date or (movie.release_date if movie else None)
    poster = item.poster_path or (movie.poster_path if movie else None)
    movie_url = (
        f"{settings.SITE_URL.rstrip('/')}/movies/{item.local_movie_id}"
        if item.local_movie_id
        else None
    )
    return RequestNotificationData(
        request_id=item.request_id,
        movie_id=item.local_movie_id,
        tmdb_id=item.external_movie_id,
        imdb_id=item.imdb_id,
        title=title,
        original_title=(item.original_title if item.original_title and item.original_title.casefold() != title.casefold() else None),
        poster_url=canonical_poster_url(poster),
        release_date=str(release_date) if release_date else None,
        year=item.release_year or (release_date.year if release_date else None),
        language=item.verified_language_name or item.verified_original_language or item.language,
        status=item.status,
        ott_platform=ott.provider if meaningful_ott else None,
        ott_release_date=str(ott.ott_release_date) if meaningful_ott and ott.ott_release_date else None,
        movie_url=movie_url,
        already_in_library=bool(item.movie_existed_at_submission or item.local_movie_id),
    )
