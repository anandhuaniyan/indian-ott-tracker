"""Public live-TMDB Deep Search endpoints."""

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.rate_limit import limit
from app.database.connection import get_db
from app.services.deep_search import DeepSearchService
from app.services.tmdb.client import TMDbRequestError


router = APIRouter(prefix="/api/v1/deep-search", tags=["Deep Search"])


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TMDbRequestError):
        if exc.status_code == 404:
            return HTTPException(404, "Live record not found")
        return HTTPException(503, "Live search is temporarily unavailable; try again shortly")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return HTTPException(404, "Live record not found")
        if status == 429:
            return HTTPException(503, "Live search is temporarily unavailable; try again shortly")
    if isinstance(exc, RuntimeError) and "not configured" in str(exc):
        return HTTPException(503, "Live search is not configured")
    return HTTPException(502, "Live search is temporarily unavailable")


def _run(request: Request, callback):
    limit(request, "deep-search", 60, 60)
    try:
        return callback()
    except HTTPException:
        raise
    except Exception as exc:
        raise _service_error(exc) from exc


@router.get("/movies")
def movies(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    year: int | None = Query(None, ge=1870, le=2200),
    language: str | None = Query(None, min_length=2, max_length=10),
    page: int = Query(1, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = q.strip()
    if not query:
        raise HTTPException(422, "Search query is required")
    return _run(request, lambda: DeepSearchService(db).search_movies(query, year=year, language=language, page=page))


@router.get("/people")
def people(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    page: int = Query(1, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = q.strip()
    if not query:
        raise HTTPException(422, "Search query is required")
    return _run(request, lambda: DeepSearchService(db).search_people(query, page=page))


@router.get("/find")
def find(
    request: Request,
    external_id: str = Query(min_length=3, max_length=32),
    db: Session = Depends(get_db),
):
    external_id = external_id.strip().lower()
    if not re.fullmatch(r"tt\d{5,12}", external_id):
        raise HTTPException(422, "Enter a valid IMDb ID such as tt1234567")
    return _run(request, lambda: DeepSearchService(db).find_imdb(external_id))


@router.get("/movies/{movie_id}")
def movie_detail(
    movie_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if movie_id <= 0:
        raise HTTPException(422, "Invalid movie ID")
    return _run(request, lambda: DeepSearchService(db).movie_detail(movie_id))


@router.get("/people/{person_id}")
def person_detail(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if person_id <= 0:
        raise HTTPException(422, "Invalid person ID")
    return _run(request, lambda: DeepSearchService(db).person_detail(person_id))
