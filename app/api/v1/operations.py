import secrets
from datetime import datetime, timezone
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
def request_movie(payload: RequestMovie, request: Request, db: Session = Depends(get_db)):
    limit(request,"movie-request",5,3600)
    local = db.query(Movie).filter(Movie.tmdb_id == payload.movie_external_id).first()
    if local:
        return JSONResponse(
            status_code=409,
            content={"detail": "This movie is already available.", "local_movie_id": local.id},
        )
    duplicate = db.query(MovieRequest).filter(
        MovieRequest.external_movie_id == payload.movie_external_id,
        MovieRequest.status.in_(["PENDING", "REVIEWING"]),
    ).first()
    if duplicate:
        return JSONResponse(
            status_code=409,
            content={"detail": "This movie has already been requested.", "request_id": duplicate.request_id, "status": duplicate.status},
        )
    item = MovieRequest(request_id=f"REQ-{secrets.token_hex(5).upper()}", movie_name=payload.movie_name.strip(), email=str(payload.email), external_movie_id=payload.movie_external_id, release_year=payload.release_year, language=payload.language, details=payload.details.strip() if payload.details else None)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.query(MovieRequest).filter(
            MovieRequest.external_movie_id == payload.movie_external_id,
            MovieRequest.status.in_(["PENDING", "REVIEWING"]),
        ).first()
        if duplicate:
            return JSONResponse(status_code=409, content={"detail": "This movie has already been requested.", "request_id": duplicate.request_id, "status": duplicate.status})
        raise
    from app.services.notification_service import NotificationService
    NotificationService(db).notify(f"New movie request: {item.movie_name} (ID {item.external_movie_id}; {item.request_id})", severity="info", fingerprint=f"movie-request:{item.request_id}")
    return {"request_id": item.request_id, "status": item.status, "movie_external_id": item.external_movie_id, "duplicate": False}

@router.get("/admin/health", dependencies=[Depends(require_admin)])
def data_health(db: Session = Depends(get_db)):
    return {"movies": db.query(Movie).count(), "missing_poster": db.query(Movie).filter(Movie.poster_path.is_(None)).count(), "missing_backdrop": db.query(Movie).filter(Movie.backdrop_path.is_(None)).count(), "missing_release_date": db.query(Movie).filter(Movie.release_date.is_(None)).count(), "missing_language": db.query(Movie).filter(Movie.original_language.is_(None)).count(), "missing_ott": db.query(Movie).outerjoin(Movie.ott_availabilities).filter_by(id=None).count(), "open_issues": db.query(DataQualityIssue).filter(DataQualityIssue.resolved_at.is_(None)).count(), "unresolved_ott_evidence": db.query(OttEvidence).filter(OttEvidence.status.in_(["UNKNOWN", "CONFLICTING", "NEEDS_REVIEW"])).count()}

@router.get("/admin/movie-requests", dependencies=[Depends(require_admin)])
def requests(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(MovieRequest)
    if status: q = q.filter(MovieRequest.status == status)
    return [{"request_id": x.request_id, "movie_name": x.movie_name, "movie_external_id": x.external_movie_id, "release_year": x.release_year, "language": x.language, "status": x.status, "created_at": x.created_at} for x in q.order_by(MovieRequest.created_at.desc()).limit(200)]
