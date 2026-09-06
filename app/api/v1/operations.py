import secrets
from datetime import date, datetime, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.admin import require_admin
from app.config.settings import settings
from app.database.connection import get_db
from app.models.movie import Movie
from app.core.rate_limit import limit
from app.models.operations import ContactRequest, DataQualityIssue, MovieRequest, OttEvidence
from app.models.research import RequestCommunication
from app.services.deep_search import DeepSearchService
from app.services.movie_requests import (
    ACTIVE_REQUEST_STATUSES,
    MovieRequestEmailService,
)
from app.services.tmdb.client import TMDbRequestError
from app.services.contact_requests import ContactRequestNotificationService
from app.services.operations import OttResearchService
from app.services.release_status import best_canonical_ott

router = APIRouter(prefix="/api/v1", tags=["Operations"])


class RequestMovie(BaseModel):
    movie_name: str = Field(min_length=2, max_length=500)
    email: str = Field(max_length=320)
    movie_external_id: int | None = Field(default=None, ge=1, le=2_147_483_647)
    release_year: int | None = Field(default=None, ge=1888, le=2100)
    language: str | None = Field(default=None, max_length=20)
    whatsapp_phone: str | None = Field(default=None, max_length=50)
    details: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("A valid email address is required")
        return value

    @field_validator("movie_name", "language", "whatsapp_phone", "details", mode="before")
    @classmethod
    def clean_optional_text(cls, value):
        return value.strip() if isinstance(value, str) else value


CONTACT_TYPES = {"WEBSITE_ISSUE", "INCORRECT_MOVIE", "INCORRECT_OTT", "ACCESS_REQUEST", "OTHER"}


class ContactSubmission(BaseModel):
    request_type: str
    name: str | None = Field(default=None, max_length=200)
    whatsapp: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)
    comment: str = Field(min_length=5, max_length=5000)
    movie_name: str | None = Field(default=None, max_length=500)
    movie_url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://")
    tmdb_id: int | None = Field(default=None, ge=1, le=2_147_483_647)
    issue_type: str | None = Field(default=None, max_length=100)
    expected_ott_platform: str | None = Field(default=None, max_length=100)
    expected_ott_release_date: date | None = None
    evidence_url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://")

    @field_validator("request_type")
    @classmethod
    def valid_type(cls, value):
        value = value.strip().upper()
        if value not in CONTACT_TYPES:
            raise ValueError("Unknown request type")
        return value

    @field_validator("email")
    @classmethod
    def optional_email(cls, value):
        if value in (None, ""):
            return None
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("name", "whatsapp", "phone", "comment", "movie_name", "issue_type", "expected_ott_platform", mode="before")
    @classmethod
    def clean_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def contact_required(self):
        if not any((self.whatsapp, self.phone, self.email)):
            raise ValueError("Provide at least one contact method: WhatsApp, phone, or email")
        return self


@router.post("/movie-requests", status_code=201)
def request_movie(
    payload: RequestMovie, request: Request, db: Session = Depends(get_db)
):
    limit(request, "movie-request", 5, 3600)
    limit(request, "movie-request-email-hour", 8, 3600, identity=payload.email)
    limit(request, "movie-request-email-day", 20, 86400, identity=payload.email)
    movie_external_id = payload.movie_external_id
    if movie_external_id is None:
        try:
            search = DeepSearchService(db).search_movies(
                payload.movie_name, year=payload.release_year, language=payload.language
            )
        except Exception as exc:
            raise HTTPException(503, "Movie search is temporarily unavailable. Please try again later.") from exc
        normalized = " ".join(payload.movie_name.casefold().split())
        exact = [
            item for item in search.get("results", [])
            if normalized in {
                " ".join(str(item.get("title") or "").casefold().split()),
                " ".join(str(item.get("original_title") or "").casefold().split()),
            }
        ]
        candidates = exact or search.get("results", [])[:5]
        if len(candidates) != 1:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Choose the matching TMDB movie so we can verify the correct title.",
                    "candidates": candidates[:5],
                },
            )
        movie_external_id = int(candidates[0]["id"])
    limit(request, "movie-request-id", 8, 3600, identity=movie_external_id)
    duplicate = (
        db.query(MovieRequest)
        .filter(
            MovieRequest.external_movie_id == movie_external_id,
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
        snapshot = DeepSearchService(db).verify_movie(movie_external_id)
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
            MovieRequest.external_movie_id == movie_external_id,
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
    local = db.query(Movie).filter(Movie.tmdb_id == movie_external_id).first()
    item = MovieRequest(
        request_id=f"REQ-{secrets.token_hex(5).upper()}",
        movie_name=payload.movie_name,
        email=str(payload.email),
        external_movie_id=movie_external_id,
        release_year=release_date.year if release_date else None,
        language=payload.language or snapshot.get("original_language"),
        whatsapp_phone=payload.whatsapp_phone or None,
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
                MovieRequest.external_movie_id == movie_external_id,
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
        release_label = item.verified_release_date or item.release_year or "Unknown"
        ott = best_canonical_ott(local) if local else None
        ott_platform = ott.provider if ott else "Researching"
        ott_date = ott.ott_release_date if ott and ott.ott_release_date else "Researching"
        ott_confidence = f"{int(ott.confidence or 0)}%" if ott else "Researching"
        message = (
            "🎬 NEW MOVIE REQUEST\n"
            f"Request ID: {item.request_id}\nMovie Name: {item.verified_title or item.movie_name}\n"
            f"Original Movie Name: {item.original_title or 'Not applicable'}\n"
            f"Year: {item.release_year or 'Unknown'}\n"
            f"Language: {item.verified_language_name or item.verified_original_language or item.language or 'Unknown'}\n"
            f"TMDB ID: {item.external_movie_id}\nIMDb ID: {item.imdb_id or 'Pending'}\n"
            f"TMDB/Theatrical Release Date: {release_label}\n"
            f"OTT Platform: {ott_platform}\nOTT Release Date: {ott_date}\nOTT Confidence: {ott_confidence}\n"
            f"Requester Email: {item.email}\nRequester WhatsApp/Phone: {item.whatsapp_phone or 'Not supplied'}\n"
            f"Comments: {item.details or 'Not supplied'}\nRequest Time: {item.created_at.isoformat()}\n"
            f"Current Status: {item.status}\nLocal Movie ID: {item.local_movie_id or 'Pending'}\n"
            f"Admin: {settings.SITE_URL.rstrip('/')}/admin/requests/{item.request_id}"
        )
        for channel in ("telegram",):
            sent = NotificationService(db).notify(
                message,
                severity="info",
                fingerprint=f"movie-request:{item.request_id}:{channel}",
                channels=(channel,),
            )
            db.add(RequestCommunication(
                movie_request_id=item.id,
                event_type="NEW_REQUEST",
                channel=channel,
                status="SENT" if sent else "NOT_CONFIGURED_OR_FAILED",
                attempt_count=1,
                last_attempt_at=datetime.now(timezone.utc),
                sent_at=datetime.now(timezone.utc) if sent else None,
                last_error=None if sent else f"{channel.title()} is not configured or delivery failed",
                fingerprint=f"movie-request:{item.request_id}:{channel}",
            ))
            db.commit()
    except Exception:
        # Requester confirmation and the committed request are independent of
        # administrator-channel availability.
        db.rollback()
    from app.services.request_discord import RequestDiscordService

    discord_delivery = RequestDiscordService(db).schedule_submission(item)
    # A request is the highest research priority. The single unified task may
    # safely import the verified TMDB identity when missing, then runs the same
    # research services used by administrator and scheduled actions.
    try:
        from app.workers.celery_app import celery_app
        celery_app.send_task("research.movie_request", args=[item.request_id], ignore_result=True)
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
        "discord_status": discord_delivery.status,
        "duplicate": False,
    }


@router.post("/contact-requests", status_code=201)
def submit_contact_request(
    payload: ContactSubmission, request: Request, db: Session = Depends(get_db)
):
    limit(request, "contact-request", 8, 3600)
    contact_identity = payload.email or payload.whatsapp or payload.phone or "anonymous"
    limit(request, "contact-request-identity", 12, 86400, identity=contact_identity)
    movie = db.query(Movie).filter_by(tmdb_id=payload.tmdb_id).first() if payload.tmdb_id else None
    item = ContactRequest(
        request_id=f"WEB-{secrets.token_hex(5).upper()}",
        request_type=payload.request_type,
        name=payload.name or None,
        whatsapp=payload.whatsapp or None,
        phone=payload.phone or None,
        email=payload.email,
        comment=payload.comment,
        movie_name=payload.movie_name or (movie.title if movie else None),
        movie_url=str(payload.movie_url) if payload.movie_url else None,
        tmdb_id=payload.tmdb_id,
        issue_type=payload.issue_type or None,
        expected_ott_platform=payload.expected_ott_platform or None,
        expected_ott_release_date=payload.expected_ott_release_date,
        evidence_url=str(payload.evidence_url) if payload.evidence_url else None,
        local_movie_id=movie.id if movie else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    if payload.request_type == "INCORRECT_OTT" and movie:
        evidence = OttResearchService(db).record_evidence(
            movie.id,
            platform=payload.expected_ott_platform,
            release_date=payload.expected_ott_release_date,
            source_url=str(payload.evidence_url) if payload.evidence_url else None,
            source_name="User report",
            source_type="user_report",
            confidence=0,
            summary=payload.comment,
            inspected=False,
            trusted=False,
            verification_method="USER_REPORT",
            allow_publication=False,
        )
        evidence.status = "NEEDS_REVIEW"
        item.ott_evidence_id = evidence.id
        db.commit()
    outcomes = ContactRequestNotificationService(db).notify_submission(item)
    return {
        "request_id": item.request_id,
        "status": item.status,
        "type": item.request_type,
        "discord_status": outcomes["discord"],
        "receipt_email_status": outcomes["email"],
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
