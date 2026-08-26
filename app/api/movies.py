from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.movie import MovieRead
from app.services.movie_service import MovieService
from app.core.admin import require_admin
from app.models.movie_metadata import ExternalId, MovieCredit, MovieImage, MovieRating, MovieReleaseDate


router = APIRouter(
    prefix="/movies",
    tags=["Movies"],
)


def _require_movie(movie_id: int, db: Session):
    movie = MovieService(db).get_movie(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie


@router.get("/", response_model=list[MovieRead])
def get_movies(
    page: int = Query(
        default=1,
        ge=1,
        description="Page number starting from 1",
    ),

    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Number of movies per page",
    ),

    language: str | None = Query(
        default=None,
        description="Language code e.g. ml, ta, te, hi",
    ),

    genre: str | None = Query(
        default=None,
        description="Genre slug e.g. action, comedy",
    ),

    year: int | None = Query(
        default=None,
        description="Release year",
    ),

    sort: str = Query(
        default="latest",
        description="Sort options: latest, rating, popular",
    ),

    db: Session = Depends(get_db),
):

    service = MovieService(db)

    return service.get_movies(
        page=page,
        page_size=page_size,
        language=language,
        genre=genre,
        year=year,
        sort=sort,
    )



@router.get(
    "/search/",
    response_model=list[MovieRead],
)
def search_movies(
    query: str,
    db: Session = Depends(get_db),
):

    service = MovieService(db)

    return service.search_movies(
        query
    )



@router.get(
    "/{movie_id}",
    response_model=MovieRead,
)
def get_movie(
    movie_id: int,
    db: Session = Depends(get_db),
):

    service = MovieService(db)

    movie = service.get_movie(
        movie_id
    )


    if movie is None:

        raise HTTPException(
            status_code=404,
            detail="Movie not found",
        )


    return movie


@router.get("/{movie_id}/cast")
def get_movie_cast(movie_id: int, db: Session = Depends(get_db)):
    _require_movie(movie_id, db)
    records = db.query(MovieCredit).filter_by(movie_id=movie_id, credit_type="cast").order_by(MovieCredit.cast_order.nullslast(), MovieCredit.id).all()
    return [{"person_id": item.person_id, "name": item.person.name, "profile_path": item.person.profile_path, "character": item.character, "order": item.cast_order} for item in records]


@router.get("/{movie_id}/crew")
def get_movie_crew(movie_id: int, db: Session = Depends(get_db)):
    _require_movie(movie_id, db)
    records = db.query(MovieCredit).filter_by(movie_id=movie_id, credit_type="crew").order_by(MovieCredit.department, MovieCredit.job, MovieCredit.id).all()
    return [{"person_id": item.person_id, "name": item.person.name, "profile_path": item.person.profile_path, "department": item.department, "job": item.job} for item in records]


@router.get("/{movie_id}/images")
def get_movie_images(movie_id: int, db: Session = Depends(get_db)):
    _require_movie(movie_id, db)
    records = db.query(MovieImage).filter_by(movie_id=movie_id).order_by(MovieImage.image_type, MovieImage.is_primary.desc(), MovieImage.id).all()
    return [{"type": item.image_type, "source": item.source, "url": item.local_path, "remote_url": item.original_url, "language": item.language, "width": item.width, "height": item.height, "is_primary": item.is_primary, "available": bool(item.local_path)} for item in records]


@router.get("/{movie_id}/releases")
def get_movie_releases(movie_id: int, db: Session = Depends(get_db)):
    _require_movie(movie_id, db)
    records = db.query(MovieReleaseDate).filter_by(movie_id=movie_id).order_by(MovieReleaseDate.release_date, MovieReleaseDate.country).all()
    return [{"country": item.country, "release_date": item.release_date, "release_type": item.release_type, "certification": item.certification, "note": item.note} for item in records]


@router.get("/{movie_id}/ratings")
def get_movie_ratings(movie_id: int, db: Session = Depends(get_db)):
    _require_movie(movie_id, db)
    records = db.query(MovieRating).filter_by(movie_id=movie_id).order_by(MovieRating.source).all()
    return [{"source": item.source, "rating": item.rating, "vote_count": item.vote_count, "last_updated_at": item.last_updated_at, "available": item.rating is not None} for item in records]


@router.get("/{movie_id}/external-ids")
def get_movie_external_ids(movie_id: int, db: Session = Depends(get_db)):
    _require_movie(movie_id, db)
    records = db.query(ExternalId).filter_by(movie_id=movie_id).order_by(ExternalId.provider).all()
    return [{"provider": item.provider, "id": item.external_id, "source_url": item.source_url} for item in records]


@router.post("/{movie_id}/enrich", response_model=MovieRead, summary="Enrich an existing movie from TMDB")
def enrich_movie(movie_id: int, db: Session = Depends(get_db), _admin: None = Depends(require_admin)):
    from app.services.movie_metadata_service import MovieMetadataService

    movie = _require_movie(movie_id, db)
    MovieMetadataService(db).enrich_movie(movie)
    return MovieService(db).get_movie(movie_id)


@router.post(
    "/{movie_id}/sync-ott",
    response_model=MovieRead,
    summary="Trigger immediate OTT availability sync for a movie",
)
def sync_movie_ott(
    movie_id: int,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin),
):
    from app.services.ott_availability_service import OttAvailabilityService

    service = MovieService(db)
    movie = service.get_movie(movie_id)

    if movie is None:
        raise HTTPException(
            status_code=404,
            detail="Movie not found",
        )

    ott_service = OttAvailabilityService(db)
    ott_service.sync_movie_ott(movie)

    # Re-fetch movie with updated availability
    return service.get_movie(movie_id)
