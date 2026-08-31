import secrets
from datetime import date, datetime, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.admin import require_admin
from app.database.connection import get_db
from app.models.movie import Movie
from app.core.rate_limit import limit
from app.models.operations import DataQualityIssue, MovieRequest, OttEvidence
from app.services.deep_search import DeepSearchService
from app.services.movie_requests import (
    ACTIVE_REQUEST_STATUSES,
    MovieRequestEmailService,
)
from app.services.tmdb.client import TMDbRequestError

router = APIRouter(prefix="/api/v1", tags=["Operations"])


class RequestMovie(BaseModel):
    movie_name: str = Field(min_length=2, max_length=500)
    email: str = Field(max_length=320)
    movie_external_id: int = Field(ge=1, le=2_147_483_647)
    release_year: int | None = Field(default=None, ge=1888, le=2100)
    language: str | None = Field(default=None, max_length=20)
    details: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("A valid email address is required")
        return value


@router.post("/movie-requests", status_code=201)
def request_movie(
    payload: RequestMovie, request: Request, db: Session = Depends(get_db)
):
    limit(request, "movie-request", 5, 3600)
    limit(request, "movie-request-email-hour", 8, 3600, identity=payload.email)
    limit(request, "movie-request-email-day", 20, 86400, identity=payload.email)
    limit(request, "movie-request-id", 8, 3600, identity=payload.movie_external_id)
    duplicate = (
        db.query(MovieRequest)
        .filter(
            MovieRequest.external_movie_id == payload.movie_external_id,
            func.lower(MovieRequest.email) == payload.email.lower(),
            MovieRequest.status.in_(ACTIVE_REQUEST_STATUSES),
        )
        .first()
    )
    if duplicate:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "You already have an active request for this movie.",
                "status": duplicate.status,
            },
        )
    try:
        snapshot = DeepSearchService(db).verify_movie(payload.movie_external_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                404, "Movie could not be found. Please check the ID or use Deep Search."
            ) from exc
        raise HTTPException(
            503,
            "Movie verification is temporarily unavailable. Please try again later.",
        ) from exc
    except TMDbRequestError as exc:
        if exc.status_code == 404:
            raise HTTPException(
                404, "Movie could not be found. Please check the ID or use Deep Search."
            ) from exc
        raise HTTPException(
            503,
            "Movie verification is temporarily unavailable. Please try again later.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            404, "Movie could not be found. Please check the ID or use Deep Search."
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            "Movie verification is temporarily unavailable. Please try again later.",
        ) from exc
    duplicate = (
        db.query(MovieRequest)
        .filter(
            MovieRequest.external_movie_id == payload.movie_external_id,
            func.lower(MovieRequest.email) == payload.email.lower(),
            MovieRequest.status.in_(ACTIVE_REQUEST_STATUSES),
        )
        .first()
    )
    if duplicate:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "You already have an active request for this movie.",
                "status": duplicate.status,
            },
        )
    release_date = None
    if snapshot.get("release_date"):
        try:
            release_date = date.fromisoformat(snapshot["release_date"])
        except (TypeError, ValueError):
            release_date = None
    verified_title = snapshot["verified_title"].strip()
    local = db.query(Movie).filter(Movie.tmdb_id == payload.movie_external_id).first()
    item = MovieRequest(
        request_id=f"REQ-{secrets.token_hex(5).upper()}",
        movie_name=verified_title,
        email=str(payload.email),
        external_movie_id=payload.movie_external_id,
        release_year=release_date.year if release_date else None,
        language=snapshot.get("original_language"),
        details=payload.details.strip() if payload.details else None,
        verified_title=verified_title,
        original_title=snapshot.get("original_title"),
        verified_release_date=release_date,
        verified_original_language=snapshot.get("original_language"),
        verified_language_name=snapshot.get("language_name"),
        poster_path=snapshot.get("poster_path"),
        backdrop_path=snapshot.get("backdrop_path"),
        verified_overview=snapshot.get("overview"),
        verified_genres=snapshot.get("genres") or [],
        verified_status=snapshot.get("status"),
        imdb_id=snapshot.get("imdb_id"),
        director=snapshot.get("director"),
        verified_at=datetime.now(timezone.utc),
        local_movie_id=local.id if local else None,
        movie_existed_at_submission=bool(local),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = (
            db.query(MovieRequest)
            .filter(
                MovieRequest.external_movie_id == payload.movie_external_id,
                func.lower(MovieRequest.email) == payload.email.lower(),
                MovieRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            )
            .first()
        )
        if duplicate:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "You already have an active request for this movie.",
                    "status": duplicate.status,
                },
            )
        raise
    email_result = MovieRequestEmailService(db).send(
        item, "confirmation", respect_cooldown=False
    )
    admin_email_result = MovieRequestEmailService(db).send(
        item, "admin_notification", respect_cooldown=False
    )
    from app.services.notification_service import NotificationService

    try:
        NotificationService(db).notify(
            f"New movie request: {item.movie_name} (ID {item.external_movie_id}; {item.request_id})",
            severity="info",
            fingerprint=f"movie-request:{item.request_id}",
            channels=("discord", "telegram"),
        )
    except Exception:
        # Requester confirmation and the committed request are independent of
        # administrator-channel availability.
        db.rollback()
    if local:
        # A request is the highest research priority. Queue the existing
        # all-purpose repair plus the independent OTT evidence collectors only
        # after the request transaction has committed.
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task("repair.movie", args=[local.id])
            celery_app.send_task("operations.ott_intelligence_movie", args=[local.id])
        except Exception:
            # Queue availability must never undo or misreport a saved request.
            pass
    return {
        "request_id": item.request_id,
        "status": item.status,
        "movie_external_id": item.external_movie_id,
        "verified_title": item.verified_title,
        "original_title": item.original_title,
        "release_date": item.verified_release_date,
        "language": item.verified_original_language,
        "language_name": item.verified_language_name,
        "poster_path": item.poster_path,
        "confirmation_email_status": email_result["status"],
        "admin_notification_email_status": admin_email_result["status"],
        "local_movie_id": item.local_movie_id,
        "duplicate": False,
    }


@router.get("/admin/health", dependencies=[Depends(require_admin)])
def data_health(db: Session = Depends(get_db)):
    return {
        "movies": db.query(Movie).count(),
        "missing_poster": db.query(Movie).filter(Movie.poster_path.is_(None)).count(),
        "missing_backdrop": db.query(Movie)
        .filter(Movie.backdrop_path.is_(None))
        .count(),
        "missing_release_date": db.query(Movie)
        .filter(Movie.release_date.is_(None))
        .count(),
        "missing_language": db.query(Movie)
        .filter(Movie.original_language.is_(None))
        .count(),
        "missing_ott": db.query(Movie)
        .outerjoin(Movie.ott_availabilities)
        .filter_by(id=None)
        .count(),
        "open_issues": db.query(DataQualityIssue)
        .filter(DataQualityIssue.resolved_at.is_(None))
        .count(),
        "unresolved_ott_evidence": db.query(OttEvidence)
        .filter(OttEvidence.status.in_(["UNKNOWN", "CONFLICTING", "NEEDS_REVIEW"]))
        .count(),
    }


@router.get("/admin/movie-requests", dependencies=[Depends(require_admin)])
def requests(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(MovieRequest)
    if status:
        q = q.filter(MovieRequest.status == status)
    return [
        {
            "request_id": x.request_id,
            "movie_name": x.movie_name,
            "movie_external_id": x.external_movie_id,
            "release_year": x.release_year,
            "language": x.language,
            "status": x.status,
            "created_at": x.created_at,
        }
        for x in q.order_by(MovieRequest.created_at.desc()).limit(200)
    ]
