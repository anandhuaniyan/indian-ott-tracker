from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.movie import MovieRead
from app.services.movie_service import MovieService


router = APIRouter(
    prefix="/movies",
    tags=["Movies"],
)


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


@router.post(
    "/{movie_id}/sync-ott",
    response_model=MovieRead,
    summary="Trigger immediate OTT availability sync for a movie",
)
def sync_movie_ott(
    movie_id: int,
    db: Session = Depends(get_db),
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