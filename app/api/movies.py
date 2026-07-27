from fastapi import APIRouter, Depends, HTTPException
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
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    service = MovieService(db)
    return service.get_movies(page, page_size)


@router.get("/search/", response_model=list[MovieRead])
def search_movies(
    query: str,
    db: Session = Depends(get_db),
):
    service = MovieService(db)
    return service.search_movies(query)


@router.get("/{movie_id}", response_model=MovieRead)
def get_movie(
    movie_id: int,
    db: Session = Depends(get_db),
):
    service = MovieService(db)

    movie = service.get_movie(movie_id)

    if movie is None:
        raise HTTPException(
            status_code=404,
            detail="Movie not found",
        )

    return movie