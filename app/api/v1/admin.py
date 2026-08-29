"""Cookie-authenticated operational administration API."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.core.rate_limit import limit
from app.core.session_auth import (
    COOKIE,
    create_session,
    require_admin_session,
    require_same_origin,
    verify_password,
)
from app.database.connection import get_db
from app.models.movie import Movie
from app.models.movie_metadata import (
    ExternalId,
    MovieCredit,
    MovieRating,
    MovieTrailer,
    Person,
)
from app.models.operations import (
    BackfillRecord,
    DataQualityIssue,
    MovieComment,
    MovieRequest,
    NotificationLog,
    OperationState,
    OttEvidence,
)
from app.models.ott_availability import OttAvailability
from app.services.image_fallback import ImageFallbackService
from app.services.languages import language_name
from app.services.movie_requests import (
    EMAIL_KINDS,
    MovieRequestAutomationService,
    MovieRequestEmailService,
)
from app.services.operations import OttResearchService, ResearchUsageService
from app.services.release_status import (
    ELIGIBILITY_LABELS,
    RELEASE_LABELS,
    ReleaseStatusService,
    best_canonical_ott,
    site_date,
)
from app.services.tmdb.movie_service import TMDbMovieService

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
REQUEST_STATUSES = {"PENDING", "REVIEWING", "FOUND", "ADDED", "REJECTED"}
OTT_STATUSES = {
    "UNKNOWN",
    "QUEUED",
    "RESEARCHING",
    "POSSIBLE",
    "CONFIRMED",
    "CONFLICTING",
    "NOT_FOUND",
    "NEEDS_REVIEW",
    "FAILED",
    "WAITING_RELEASE",
    "METADATA_REPAIR",
    "TOO_OLD",
    "ELIGIBLE",
}
BACKFILL_TASKS = {
    "metadata": "tmdb.metadata_backfill",
    "people": "tmdb.person_backfill",
    "images": "operations.image_backfill",
    "imdb-ids": "ratings.imdb_id_backfill",
    "trailers": "tmdb.trailer_backfill",
    "imdb": "ratings.imdb_backfill",
    "ott": "operations.ott_backfill",
    "all": "operations.repair_orchestrator",
}


class Login(BaseModel):
    password: str = Field(min_length=8, max_length=512)


class RequestStatus(BaseModel):
    status: str
    public_rejection_reason: str | None = Field(default=None, max_length=1000)
    internal_reason: str | None = Field(default=None, max_length=2000)


class OttAction(BaseModel):
    action: str = Field(pattern="^(requeue|retry|needs_review)$")


class OttManualVerification(BaseModel):
    platform: str = Field(min_length=2, max_length=100)
    ott_release_date: date
    source_url: str = Field(min_length=10, max_length=1000, pattern=r"^https://")
    source_name: str | None = Field(default=None, max_length=200)
    country: str = Field(default="IN", min_length=2, max_length=10)
    summary: str | None = Field(default=None, max_length=2000)


class OttEvidenceAction(BaseModel):
    action: str = Field(pattern="^(trust|reject)$")
    reason: str | None = Field(default=None, max_length=1000)


class CommentModeration(BaseModel):
    status: str = Field(pattern="^(APPROVED|HIDDEN|REJECTED)$")
    reason: str | None = Field(default=None, max_length=1000)


def _pagination(total: int, page: int, page_size: int):
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


def _ott_coverage(db: Session) -> dict:
    today = site_date()
    recent_cutoff = today - timedelta(days=30)
    total = db.query(Movie).count()
    with_platform = (
        db.query(func.count(func.distinct(OttAvailability.movie_id)))
        .filter(
            OttAvailability.country == "IN",
            OttAvailability.provider.is_not(None),
            OttAvailability.provider != "",
        )
        .scalar()
        or 0
    )
    with_date = (
        db.query(func.count(func.distinct(OttAvailability.movie_id)))
        .filter(
            OttAvailability.country == "IN",
            OttAvailability.ott_release_date.is_not(None),
        )
        .scalar()
        or 0
    )
    confirmed_date = (
        db.query(func.count(func.distinct(OttAvailability.movie_id)))
        .filter(
            OttAvailability.country == "IN",
            OttAvailability.ott_release_date.is_not(None),
            OttAvailability.verification_status == "CONFIRMED",
        )
        .scalar()
        or 0
    )
    platform_movie_ids = select(OttAvailability.movie_id).where(
        OttAvailability.country == "IN", OttAvailability.provider.is_not(None)
    )
    confirmed_movie_ids = select(OttAvailability.movie_id).where(
        OttAvailability.country == "IN",
        OttAvailability.ott_release_date.is_not(None),
        OttAvailability.verification_status == "CONFIRMED",
    )
    counts = {
        "total_movies": total,
        "movies_with_ott_platform": with_platform,
        "movies_with_ott_release_date": with_date,
        "movies_with_confirmed_ott_date": confirmed_date,
        "movies_with_platform_but_missing_date": db.query(Movie)
        .filter(Movie.id.in_(platform_movie_ids), ~Movie.id.in_(confirmed_movie_ids))
        .count(),
        "movies_with_unknown_ott": max(0, total - with_platform),
        "movies_awaiting_research": db.query(
            func.count(func.distinct(OttEvidence.movie_id))
        )
        .filter(
            OttEvidence.source_url.is_(None),
            OttEvidence.status.in_(
                ["UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "NOT_FOUND", "FAILED"]
            ),
        )
        .scalar()
        or 0,
        "movies_with_conflicting_evidence": db.query(
            func.count(func.distinct(OttEvidence.movie_id))
        )
        .filter(OttEvidence.status == "CONFLICTING", OttEvidence.rejected_at.is_(None))
        .scalar()
        or 0,
        "movies_needing_review": db.query(
            func.count(func.distinct(OttAvailability.movie_id))
        )
        .filter(OttAvailability.verification_status == "NEEDS_REVIEW")
        .scalar()
        or 0,
        "upcoming_confirmed_ott_releases": db.query(
            func.count(func.distinct(OttAvailability.movie_id))
        )
        .filter(
            OttAvailability.verification_status == "CONFIRMED",
            OttAvailability.ott_release_date > today,
        )
        .scalar()
        or 0,
        "recently_released_on_ott": db.query(
            func.count(func.distinct(OttAvailability.movie_id))
        )
        .filter(
            OttAvailability.verification_status == "CONFIRMED",
            OttAvailability.ott_release_date >= recent_cutoff,
            OttAvailability.ott_release_date <= today,
        )
        .scalar()
        or 0,
    }
    counts["percentages"] = {
        key: round(value * 100 / total, 2) if total else 0.0
        for key, value in counts.items()
        if key != "total_movies"
    }
    return counts


def _imdb_health(db: Session) -> dict:
    total = db.query(func.count(Movie.id)).scalar() or 0
    with_id = (
        db.query(func.count(func.distinct(ExternalId.movie_id)))
        .filter(func.lower(ExternalId.provider) == "imdb")
        .scalar()
        or 0
    )
    available = (
        db.query(func.count(func.distinct(MovieRating.movie_id)))
        .filter(
            func.lower(MovieRating.source) == "imdb", MovieRating.rating.is_not(None)
        )
        .scalar()
        or 0
    )
    rating_rows = (
        db.query(func.count(func.distinct(MovieRating.movie_id)))
        .filter(func.lower(MovieRating.source) == "imdb")
        .scalar()
        or 0
    )
    pending_rows = (
        db.query(func.count(func.distinct(MovieRating.movie_id)))
        .filter(
            func.lower(MovieRating.source) == "imdb",
            MovieRating.status.in_(
                [
                    "PENDING",
                    "NOT_YET_RATED",
                    "TEMPORARY_FAILURE",
                    "NOT_FOUND",
                    "BLOCKED_BY_QUOTA",
                ]
            ),
        )
        .scalar()
        or 0
    )
    null_rows = (
        db.query(func.count(func.distinct(MovieRating.movie_id)))
        .filter(func.lower(MovieRating.source) == "imdb", MovieRating.rating.is_(None))
        .scalar()
        or 0
    )
    failures = (
        db.query(func.count(func.distinct(MovieRating.movie_id)))
        .filter(
            func.lower(MovieRating.source) == "imdb",
            MovieRating.status.in_(["TEMPORARY_FAILURE", "NOT_FOUND", "INVALID_ID"]),
        )
        .scalar()
        or 0
    )
    states = {
        item.name: item
        for item in db.query(OperationState).filter(
            OperationState.name.in_(
                [
                    "ratings.imdb_id_backfill",
                    "ratings.imdb_backfill",
                    "imdb_rating_refresh",
                ]
            )
        )
    }
    rating_state = states.get("ratings.imdb_backfill") or states.get(
        "imdb_rating_refresh"
    )
    id_state = states.get("ratings.imdb_id_backfill")
    quota_blocked = any(
        item
        and item.status == "BLOCKED"
        and any(
            word in (item.last_error or "").lower() for word in ("quota", "rate limit")
        )
        for item in states.values()
    )
    return {
        "movies_total": total,
        "imdb_id_available": with_id,
        "imdb_id_missing": max(0, total - with_id),
        "imdb_rating_available": available,
        "imdb_id_available_but_rating_missing": max(0, with_id - available),
        "imdb_rating_pending": max(0, with_id - rating_rows) + pending_rows,
        "imdb_rating_null_not_yet_rated": null_rows,
        "imdb_provider_failures": failures,
        "imdb_provider_configured": bool(
            settings.IMDB_RATING_PROVIDER
            and settings.IMDB_RATING_API_URL
            and settings.IMDB_RATING_API_KEY
        ),
        "imdb_provider_quota_or_rate_limited": quota_blocked,
        "imdb_id_backfill_status": id_state.status if id_state else "IDLE",
        "imdb_rating_backfill_status": rating_state.status if rating_state else "IDLE",
        "imdb_rating_backfill_processed": (
            rating_state.processed_count if rating_state else 0
        ),
        "imdb_rating_backfill_total": (
            rating_state.total_count if rating_state else with_id
        ),
        "last_successful_rating_refresh": (
            rating_state.last_success_at if rating_state else None
        ),
    }


@router.post("/login")
def login(payload: Login, response: Response, request: Request):
    limit(request, "admin-login", 5, 300)
    if not verify_password(payload.password):
        raise HTTPException(401, "Invalid credentials")
    response.set_cookie(
        COOKIE,
        create_session(),
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="strict",
        max_age=28800,
        path="/",
    )
    return {"authenticated": True}


@router.post("/logout", dependencies=[Depends(require_same_origin)])
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}


@router.get("/session")
def session(_: None = Depends(require_admin_session)):
    return {"authenticated": True}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    open_filter = DataQualityIssue.resolved_at.is_(None)
    recent_notifications = (
        db.query(NotificationLog)
        .order_by(NotificationLog.created_at.desc())
        .limit(10)
        .all()
    )
    jobs = db.query(OperationState).order_by(OperationState.name).all()
    return {
        "total_movies": db.query(Movie).count(),
        "movies_with_issues": db.query(
            func.count(func.distinct(DataQualityIssue.movie_id))
        )
        .filter(open_filter)
        .scalar()
        or 0,
        "open_issues": db.query(DataQualityIssue).filter(open_filter).count(),
        "image_issues": db.query(DataQualityIssue)
        .filter(
            open_filter,
            DataQualityIssue.issue_type.in_(
                [
                    "missing_poster",
                    "broken_poster",
                    "missing_backdrop",
                    "broken_backdrop",
                    "missing_logo",
                    "missing_profile",
                    "broken_profile",
                    "image_unresolved",
                ]
            ),
        )
        .count(),
        "missing_ott": db.query(DataQualityIssue)
        .filter(
            open_filter,
            DataQualityIssue.issue_type.in_(
                ["missing_ott", "missing_ott_provider", "missing_ott_release_date"]
            ),
        )
        .count(),
        "conflicting_ott": db.query(OttEvidence)
        .filter(OttEvidence.status == "CONFLICTING")
        .count(),
        "ott_queue": db.query(OttEvidence)
        .filter(
            OttEvidence.status.in_(
                [
                    "UNKNOWN",
                    "QUEUED",
                    "RESEARCHING",
                    "POSSIBLE",
                    "NOT_FOUND",
                    "CONFLICTING",
                    "NEEDS_REVIEW",
                    "FAILED",
                ]
            )
        )
        .count(),
        "failed_research": db.query(OttEvidence)
        .filter(OttEvidence.status == "FAILED")
        .count(),
        "pending_requests": db.query(MovieRequest)
        .filter(MovieRequest.status == "PENDING")
        .count(),
        "pending_comments": db.query(MovieComment)
        .filter(MovieComment.status == "PENDING")
        .count(),
        "recent_notifications": [
            {
                "id": x.id,
                "timestamp": x.created_at,
                "channel": x.channel,
                "severity": x.severity,
                "message": x.message,
            }
            for x in recent_notifications
        ],
        "jobs": [
            {
                "task": x.name,
                "cursor": x.cursor,
                "processed_count": x.processed_count,
                "last_success": x.last_success_at,
                "last_failure": x.last_failure_at,
                "last_error": x.last_error,
            }
            for x in jobs
        ],
    }


@router.get("/requests")
def requests(
    search: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(MovieRequest)
    if status:
        if status not in REQUEST_STATUSES:
            raise HTTPException(422, "Unknown request status")
        query = query.filter(MovieRequest.status == status)
    if search:
        term = f"%{search.strip()}%"
        request_id_match = (
            MovieRequest.external_movie_id == int(search)
            if search.strip().isdigit()
            else False
        )
        query = query.filter(
            or_(
                MovieRequest.movie_name.ilike(term),
                MovieRequest.email.ilike(term),
                MovieRequest.request_id.ilike(term),
                request_id_match,
            )
        )
    total = query.count()
    rows = (
        query.order_by(MovieRequest.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return _pagination(total, page, page_size) | {
        "items": [_request(item) for item in rows]
    }


def _request(item: MovieRequest):
    now = datetime.now(timezone.utc)
    created = (
        item.created_at
        if item.created_at.tzinfo
        else item.created_at.replace(tzinfo=timezone.utc)
    )
    age_seconds = max(0, int((now - created).total_seconds()))
    target_at = created + timedelta(hours=48)
    return {
        "request_id": item.request_id,
        "movie_name": item.movie_name,
        "verified_title": item.verified_title or item.movie_name,
        "original_title": item.original_title,
        "movie_external_id": item.external_movie_id,
        "email": item.email,
        "release_year": item.release_year,
        "release_date": item.verified_release_date,
        "language": item.verified_original_language or item.language,
        "language_name": language_name(
            item.verified_original_language or item.language,
            item.verified_language_name,
        ),
        "poster_path": item.poster_path,
        "backdrop_path": item.backdrop_path,
        "overview": item.verified_overview,
        "genres": item.verified_genres or [],
        "movie_status": item.verified_status,
        "imdb_id": item.imdb_id,
        "director": item.director,
        "verified_at": item.verified_at,
        "details": item.details,
        "status": item.status,
        "local_movie_id": item.local_movie_id,
        "movie_existed_at_submission": item.movie_existed_at_submission,
        "public_rejection_reason": item.public_rejection_reason,
        "internal_rejection_reason": item.internal_rejection_reason,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "age_seconds": age_seconds,
        "target_at": target_at,
        "target_seconds": int((target_at - now).total_seconds()),
        "sla_36_notified_at": item.sla_36_notified_at,
        "sla_48_notified_at": item.sla_48_notified_at,
        "emails": {
            kind: {
                "status": getattr(item, f"{kind}_email_status"),
                "sent_at": getattr(item, f"{kind}_email_sent_at"),
                "last_error": getattr(item, f"{kind}_email_last_error"),
                "last_attempt_at": getattr(item, f"{kind}_email_last_attempt_at"),
                "attempt_count": getattr(item, f"{kind}_email_attempt_count"),
            }
            for kind in EMAIL_KINDS
        },
    }


@router.get("/requests/{request_id}")
def request_detail(
    request_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    item = db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item:
        raise HTTPException(404, "Request not found")
    return _request(item)


@router.patch("/requests/{request_id}", dependencies=[Depends(require_same_origin)])
def update_request(
    request_id: str,
    payload: RequestStatus,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    if payload.status not in REQUEST_STATUSES:
        raise HTTPException(422, "Unknown request status")
    item = db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item:
        raise HTTPException(404, "Request not found")
    item.public_rejection_reason = payload.public_rejection_reason
    item.internal_rejection_reason = payload.internal_reason
    if payload.status == "ADDED":
        movie = db.query(Movie).filter(Movie.tmdb_id == item.external_movie_id).first()
        if not movie:
            raise HTTPException(
                409,
                "Cannot mark this request added until the matching movie exists locally",
            )
        MovieRequestAutomationService(db)._complete(item, movie, force=True)
        return _request(item)
    item.status = payload.status
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "This requester already has an active request for this movie"
        ) from exc
    if payload.status == "REJECTED":
        MovieRequestEmailService(db).send(item, "rejection", respect_cooldown=False)
    return _request(item)


@router.post(
    "/requests/{request_id}/emails/{kind}/retry",
    dependencies=[Depends(require_same_origin)],
)
def retry_request_email(
    request_id: str,
    kind: str,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    if kind not in EMAIL_KINDS:
        raise HTTPException(422, "Unknown email type")
    item = db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item:
        raise HTTPException(404, "Request not found")
    if kind == "completion":
        movie = db.query(Movie).filter(Movie.tmdb_id == item.external_movie_id).first()
        if item.status != "ADDED" or not movie or item.local_movie_id != movie.id:
            raise HTTPException(409, "Completion email requires an added local movie")
    if kind == "rejection" and item.status != "REJECTED":
        raise HTTPException(409, "Rejection email requires a rejected request")
    limit(
        request, "movie-request-email-retry", 6, 3600, identity=f"{request_id}:{kind}"
    )
    result = MovieRequestEmailService(db).send(item, kind)
    if result.get("skipped") == "already_sent":
        raise HTTPException(409, "This email was already sent")
    if result.get("skipped") == "cooldown":
        raise HTTPException(429, "Please wait before retrying this email")
    return result | {"request": _request(item)}


@router.get("/comments")
def comments(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(MovieComment, Movie).join(Movie, Movie.id == MovieComment.movie_id)
    if status:
        if status not in {"PENDING", "APPROVED", "HIDDEN", "REJECTED"}:
            raise HTTPException(422, "Unknown comment status")
        query = query.filter(MovieComment.status == status)
    total = query.count()
    rows = (
        query.order_by(MovieComment.created_at.desc(), MovieComment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return _pagination(total, page, page_size) | {
        "items": [
            {
                "id": item.id,
                "movie_id": movie.id,
                "movie_title": movie.title,
                "display_name": item.display_name,
                "email": item.email,
                "comment": item.comment_text,
                "status": item.status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "moderated_at": item.moderated_at,
                "moderation_reason": item.moderation_reason,
            }
            for item, movie in rows
        ]
    }


@router.patch("/comments/{comment_id}", dependencies=[Depends(require_same_origin)])
def moderate_comment(
    comment_id: int,
    payload: CommentModeration,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    item = db.get(MovieComment, comment_id)
    if not item:
        raise HTTPException(404, "Comment not found")
    item.status = payload.status
    item.moderation_reason = payload.reason.strip() if payload.reason else None
    item.moderated_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": item.id, "status": item.status, "moderated_at": item.moderated_at}


@router.delete("/comments/{comment_id}", dependencies=[Depends(require_same_origin)])
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    item = db.get(MovieComment, comment_id)
    if not item:
        raise HTTPException(404, "Comment not found")
    db.delete(item)
    db.commit()
    return {"deleted": True, "id": comment_id}


@router.get("/data-health")
def data_health(
    issue_type: str | None = None,
    severity: str | None = None,
    status: str = "open",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(DataQualityIssue, Movie).outerjoin(
        Movie, Movie.id == DataQualityIssue.movie_id
    )
    if status == "open":
        query = query.filter(DataQualityIssue.resolved_at.is_(None))
    elif status == "resolved":
        query = query.filter(DataQualityIssue.resolved_at.is_not(None))
    if issue_type:
        query = query.filter(DataQualityIssue.issue_type == issue_type)
    if severity:
        query = query.filter(DataQualityIssue.severity == severity)
    total = query.count()
    rows = (
        query.order_by(DataQualityIssue.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return _pagination(total, page, page_size) | {
        "imdb": _imdb_health(db),
        "ott": _ott_coverage(db),
        "items": [
            {
                "id": issue.id,
                "movie_id": issue.movie_id,
                "movie": movie.title if movie else None,
                "issue_type": issue.issue_type,
                "severity": issue.severity,
                "description": issue.detail,
                "created_at": issue.created_at,
                "status": "resolved" if issue.resolved_at else "open",
            }
            for issue, movie in rows
        ],
    }


@router.get("/images")
def images(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    image_types = [
        "missing_poster",
        "broken_poster",
        "missing_backdrop",
        "broken_backdrop",
        "missing_logo",
        "missing_profile",
        "broken_profile",
        "image_recovered",
        "image_unresolved",
    ]
    query = (
        db.query(DataQualityIssue, Movie, Person)
        .outerjoin(Movie, Movie.id == DataQualityIssue.movie_id)
        .outerjoin(Person, Person.id == DataQualityIssue.person_id)
        .filter(DataQualityIssue.issue_type.in_(image_types))
    )
    if status == "recovered":
        query = query.filter(DataQualityIssue.resolved_at.is_not(None))
    elif status == "unresolved":
        query = query.filter(DataQualityIssue.resolved_at.is_(None))
    total = query.count()
    rows = (
        query.order_by(DataQualityIssue.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    counts = dict(
        db.query(DataQualityIssue.issue_type, func.count(DataQualityIssue.id))
        .filter(
            DataQualityIssue.issue_type.in_(image_types),
            DataQualityIssue.resolved_at.is_(None),
        )
        .group_by(DataQualityIssue.issue_type)
        .all()
    )
    return _pagination(total, page, page_size) | {
        "counts": counts,
        "items": [
            {
                "id": issue.id,
                "movie_id": issue.movie_id,
                "person_id": issue.person_id,
                "subject": movie.title if movie else person.name if person else None,
                "type": issue.issue_type,
                "description": issue.detail,
                "status": "recovered" if issue.resolved_at else "unresolved",
                "updated_at": issue.updated_at,
            }
            for issue, movie, person in rows
        ],
    }


@router.post("/images/{movie_id}/retry", dependencies=[Depends(require_same_origin)])
def retry_image(
    movie_id: int,
    image_type: str = "poster",
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    return ImageFallbackService(db).recover_movie(movie, image_type)


@router.post(
    "/images/people/{person_id}/retry", dependencies=[Depends(require_same_origin)]
)
def retry_profile(
    person_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    return ImageFallbackService(db).recover_person(person)


@router.get("/ott-research")
def ott_research(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    latest_id = (
        select(func.max(OttEvidence.id))
        .where(OttEvidence.movie_id == Movie.id, OttEvidence.source_url.is_(None))
        .correlate(Movie)
        .scalar_subquery()
    )
    query = (
        db.query(Movie, OttEvidence)
        .options(selectinload(Movie.ott_availabilities))
        .outerjoin(OttEvidence, OttEvidence.id == latest_id)
        .filter(Movie.ott_research_eligibility.is_not(None))
    )
    if status:
        if status not in OTT_STATUSES:
            raise HTTPException(422, "Unknown OTT status")
        if status == "ELIGIBLE":
            query = query.filter(Movie.ott_research_eligibility == "ELIGIBLE")
        elif status == "WAITING_RELEASE":
            query = query.filter(
                Movie.ott_research_eligibility.in_(["WAITING_RELEASE", "MIN_DELAY"])
            )
        else:
            query = query.filter(OttEvidence.status == status)
    total = query.count()
    rows = (
        query.order_by(
            Movie.theatrical_release_date.desc().nullslast(), Movie.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for movie, evidence in rows:
        canonical = best_canonical_ott(movie)
        items.append(
            {
                "id": evidence.id if evidence else None,
                "movie_id": movie.id,
                "movie": movie.title,
                "poster": movie.poster_path,
                "original_language": movie.original_language,
                "theatrical_release_date": movie.theatrical_release_date,
                "release_status": RELEASE_LABELS.get(
                    movie.release_status_code, "Unknown"
                ),
                "eligibility": movie.ott_research_eligibility,
                "eligibility_label": ELIGIBILITY_LABELS.get(
                    movie.ott_research_eligibility, "Unclassified"
                ),
                "status": evidence.status if evidence else "NOT_QUEUED",
                "verification_status": (
                    canonical.verification_status if canonical else "UNKNOWN"
                ),
                "platform": (
                    canonical.provider
                    if canonical
                    else evidence.platform if evidence else None
                ),
                "date": (
                    canonical.ott_release_date
                    if canonical and canonical.verification_status == "CONFIRMED"
                    else evidence.release_date if evidence else None
                ),
                "source": (
                    evidence.source_title
                    if evidence
                    else canonical.source_type if canonical else None
                ),
                "url": (
                    evidence.source_url
                    if evidence
                    else canonical.source_url if canonical else None
                ),
                "confidence": (
                    evidence.confidence
                    if evidence
                    else canonical.confidence if canonical else 0
                ),
                "attempts": evidence.attempts if evidence else 0,
                "last_check": evidence.last_checked if evidence else None,
                "next_check": evidence.next_check if evidence else None,
                "error": evidence.notes if evidence else None,
                "sources": db.query(OttEvidence)
                .filter(
                    OttEvidence.movie_id == movie.id,
                    OttEvidence.source_url.is_not(None),
                )
                .count(),
                "manually_verified": bool(canonical and canonical.manually_verified),
            }
        )
    usage = ResearchUsageService(db)
    return _pagination(total, page, page_size) | {
        "items": items,
        "coverage": _ott_coverage(db),
        "daily_usage": usage.daily_snapshot(),
        "tavily_usage": usage.monthly_snapshot(),
    }


@router.get("/ott-research/movies/{movie_id}")
def ott_research_detail(
    movie_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    movie = (
        db.query(Movie)
        .options(selectinload(Movie.ott_availabilities))
        .filter_by(id=movie_id)
        .first()
    )
    if not movie:
        raise HTTPException(404, "Movie not found")
    evidence = (
        db.query(OttEvidence)
        .filter(OttEvidence.movie_id == movie_id, OttEvidence.source_url.is_not(None))
        .order_by(OttEvidence.created_at.desc(), OttEvidence.id.desc())
        .all()
    )
    canonical = best_canonical_ott(movie)
    return {
        "movie": {
            "id": movie.id,
            "title": movie.title,
            "original_title": movie.original_title,
            "poster": movie.poster_path,
            "original_language": movie.original_language,
            "theatrical_release_date": movie.theatrical_release_date,
        },
        "canonical": (
            {
                "id": canonical.id,
                "platform": canonical.provider,
                "ott_release_date": (
                    canonical.ott_release_date
                    if canonical.verification_status == "CONFIRMED"
                    else None
                ),
                "country": canonical.country,
                "availability_type": canonical.watch_type,
                "availability_status": canonical.status,
                "verification_status": canonical.verification_status,
                "source_type": canonical.source_type,
                "source_url": canonical.source_url,
                "confidence": canonical.confidence,
                "last_verified": canonical.verified_at,
                "manually_verified": canonical.manually_verified,
            }
            if canonical
            else None
        ),
        "evidence": [
            {
                "id": item.id,
                "source_name": item.source_name,
                "source_url": item.source_url,
                "source_type": item.source_type,
                "source_title": item.source_title,
                "source_published_at": item.source_published_at,
                "platform_found": item.platform,
                "release_date_found": item.release_date,
                "country": item.country,
                "evidence_summary": item.summary,
                "confidence": item.confidence,
                "checked_at": item.last_checked,
                "inspected_at": item.inspected_at,
                "result_status": item.status,
                "trusted": item.trusted,
                "manually_verified": item.manually_verified,
                "rejected_at": item.rejected_at,
                "rejection_reason": item.rejection_reason,
            }
            for item in evidence
        ],
    }


@router.post(
    "/ott-research/movies/{movie_id}/verify",
    dependencies=[Depends(require_same_origin)],
)
def verify_ott_manually(
    movie_id: int,
    payload: OttManualVerification,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    if not db.get(Movie, movie_id):
        raise HTTPException(404, "Movie not found")
    evidence = OttResearchService(
        db, settings.OTT_CONFIRMATION_THRESHOLD
    ).manually_verify(
        movie_id,
        platform=payload.platform,
        release_date=payload.ott_release_date,
        source_url=payload.source_url,
        source_name=payload.source_name,
        country=payload.country,
        summary=payload.summary,
    )
    db.query(OttEvidence).filter(
        OttEvidence.movie_id == movie_id,
        OttEvidence.source_url.is_(None),
    ).update(
        {
            "status": "CONFIRMED",
            "last_checked": datetime.now(timezone.utc),
            "next_check": None,
        },
        synchronize_session=False,
    )
    db.commit()
    return {
        "movie_id": movie_id,
        "evidence_id": evidence.id,
        "status": "CONFIRMED",
        "manually_verified": True,
    }


@router.patch(
    "/ott-evidence/{evidence_id}", dependencies=[Depends(require_same_origin)]
)
def update_ott_evidence(
    evidence_id: int,
    payload: OttEvidenceAction,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
    evidence = db.get(OttEvidence, evidence_id)
    if not evidence or not evidence.source_url:
        raise HTTPException(404, "OTT evidence not found")
    if payload.action == "reject":
        evidence = service.reject_evidence(evidence_id, payload.reason)
    else:
        evidence.trusted = True
        service.evaluate_movie(evidence.movie_id)
        db.commit()
    return {
        "id": evidence.id,
        "status": evidence.status,
        "trusted": evidence.trusted,
        "rejected_at": evidence.rejected_at,
    }


@router.post(
    "/ott-research/{evidence_id}/action", dependencies=[Depends(require_same_origin)]
)
def ott_action(
    evidence_id: int,
    payload: OttAction,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    item = db.get(OttEvidence, evidence_id)
    if not item:
        raise HTTPException(404, "Research item not found")
    if payload.action != "needs_review":
        movie = db.get(Movie, item.movie_id)
        _, eligibility, _ = ReleaseStatusService(db).classify_movie(movie)
        if eligibility.code != "ELIGIBLE":
            db.commit()
            raise HTTPException(409, eligibility.label)
    item.status = "NEEDS_REVIEW" if payload.action == "needs_review" else "QUEUED"
    item.next_check = datetime.now(timezone.utc)
    item.notes = None if payload.action == "retry" else item.notes
    db.commit()
    return {"id": item.id, "status": item.status, "next_check": item.next_check}


@router.get("/jobs")
def jobs(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    return [
        {
            "task": item.name,
            "status": item.status,
            "last_success": item.last_success_at,
            "last_failure": item.last_failure_at,
            "last_error": item.last_error,
            "cursor": item.cursor,
            "processed_count": item.processed_count,
            "total_count": item.total_count,
            "completed_at": item.completed_at,
            "remaining": (
                max(0, item.total_count - item.processed_count)
                if item.total_count
                else None
            ),
            "progress": "complete" if item.status == "COMPLETE" else "resumable",
        }
        for item in db.query(OperationState).order_by(OperationState.name)
    ]


@router.get("/backfills")
def backfills(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    states = {item.name: item for item in db.query(OperationState).all()}
    operations = (
        "tmdb.metadata_backfill",
        "tmdb.person_backfill",
        "operations.image_backfill",
        "tmdb.trailer_backfill",
        "ratings.imdb_id_backfill",
        "ratings.imdb_backfill",
        "release_status_classification",
        "release_status_classification_v2",
        "operations.ott_eligibility_backfill",
        "operations.ott_backfill",
        "operations.repair_orchestrator",
    )
    progress = []
    for name in operations:
        state = states.get(name)
        failures = (
            db.query(BackfillRecord).filter_by(operation=name, status="FAILED").count()
        )
        progress.append(
            {
                "operation": name,
                "status": state.status if state else "IDLE",
                "cursor": state.cursor if state else 0,
                "processed": state.processed_count if state else 0,
                "total": state.total_count if state else 0,
                "remaining": (
                    max(0, state.total_count - state.processed_count)
                    if state and state.total_count
                    else None
                ),
                "failed": failures,
                "last_success": state.last_success_at if state else None,
                "last_failure": state.last_failure_at if state else None,
                "last_error": state.last_error if state else None,
                "completed_at": state.completed_at if state else None,
            }
        )
    total_movies = db.query(Movie).count()
    total_people = db.query(Person).count()
    imdb_health = _imdb_health(db)
    return {
        "progress": progress,
        "coverage": {
            "movies": total_movies,
            "movies_with_cast": db.query(
                func.count(func.distinct(MovieCredit.movie_id))
            )
            .filter(MovieCredit.credit_type == "cast")
            .scalar()
            or 0,
            "movies_with_crew": db.query(
                func.count(func.distinct(MovieCredit.movie_id))
            )
            .filter(MovieCredit.credit_type == "crew")
            .scalar()
            or 0,
            "movies_with_posters": db.query(Movie)
            .filter(Movie.poster_path.is_not(None), Movie.poster_path != "")
            .count(),
            "movies_with_backdrops": db.query(Movie)
            .filter(Movie.backdrop_path.is_not(None), Movie.backdrop_path != "")
            .count(),
            "movies_with_trailers": db.query(
                func.count(func.distinct(MovieTrailer.movie_id))
            )
            .filter(MovieTrailer.is_primary.is_(True))
            .scalar()
            or 0,
            "people": total_people,
            "people_with_profiles": db.query(Person)
            .filter(Person.profile_path.is_not(None), Person.profile_path != "")
            .count(),
            "movies_with_imdb_id": imdb_health["imdb_id_available"],
            "movies_missing_imdb_id": imdb_health["imdb_id_missing"],
            "movies_with_imdb_rating": imdb_health["imdb_rating_available"],
            "movies_with_imdb_id_without_rating": imdb_health[
                "imdb_id_available_but_rating_missing"
            ],
            "imdb_rating_pending": imdb_health["imdb_rating_pending"],
            "imdb_rating_null_not_yet_rated": imdb_health[
                "imdb_rating_null_not_yet_rated"
            ],
            "imdb_provider_failures": imdb_health["imdb_provider_failures"],
            "movies_with_ott": db.query(
                func.count(func.distinct(OttAvailability.movie_id))
            ).scalar()
            or 0,
            "movies_with_ott_date": db.query(
                func.count(func.distinct(OttAvailability.movie_id))
            )
            .filter(OttAvailability.ott_release_date.is_not(None))
            .scalar()
            or 0,
            "released_movies": db.query(Movie)
            .filter(Movie.release_status_code == "THEATRICALLY_RELEASED")
            .count(),
            "upcoming_movies": db.query(Movie)
            .filter(Movie.release_status_code == "UPCOMING")
            .count(),
            "direct_to_ott_movies": db.query(Movie)
            .filter(Movie.release_status_code == "DIRECT_TO_OTT")
            .count(),
            "ott_research_eligible": db.query(Movie)
            .filter(Movie.ott_research_eligibility == "ELIGIBLE")
            .count(),
            "ott_waiting_for_release": db.query(Movie)
            .filter(
                Movie.ott_research_eligibility.in_(["WAITING_RELEASE", "MIN_DELAY"])
            )
            .count(),
            "ott_queued": db.query(OttEvidence)
            .filter(
                OttEvidence.status.in_(
                    [
                        "UNKNOWN",
                        "QUEUED",
                        "RESEARCHING",
                        "POSSIBLE",
                        "NOT_FOUND",
                        "CONFLICTING",
                        "NEEDS_REVIEW",
                        "FAILED",
                    ]
                )
            )
            .count(),
            "ott_confirmed": db.query(OttEvidence)
            .filter(OttEvidence.status == "CONFIRMED")
            .count(),
        },
        "configuration": {
            "tmdb": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
            "imdb": bool(
                settings.IMDB_RATING_PROVIDER
                and settings.IMDB_RATING_API_URL
                and settings.IMDB_RATING_API_KEY
            ),
            "google_ott_search": bool(
                settings.GOOGLE_SEARCH_API_KEY and settings.GOOGLE_SEARCH_ENGINE_ID
            ),
            "generic_ott_search": bool(
                settings.OTT_SEARCH_API_URL and settings.OTT_SEARCH_API_KEY
            ),
            "tavily": bool(
                settings.TAVILY_API_KEY
                or (
                    settings.OTT_RESEARCH_PROVIDER.lower() == "tavily"
                    and settings.OTT_SEARCH_API_KEY
                )
            ),
        },
        "imdb": imdb_health,
    }


@router.post(
    "/backfills/{operation}/start", dependencies=[Depends(require_same_origin)]
)
def start_backfill(
    operation: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    task_name = BACKFILL_TASKS.get(operation)
    if not task_name:
        raise HTTPException(422, "Unknown backfill")
    state_name = (
        "operations.ott_eligibility_backfill" if operation == "ott" else task_name
    )
    state = db.query(OperationState).filter_by(name=state_name).first()
    if state and state.status == "COMPLETE":
        return {
            "queued": False,
            "task": task_name,
            "status": "COMPLETE",
            "detail": "Backfill already completed; it was not restarted",
        }
    if (
        state
        and state.status == "RUNNING"
        and state.last_success_at
        and state.last_success_at >= datetime.now(timezone.utc) - timedelta(minutes=15)
    ):
        return {
            "queued": False,
            "task": task_name,
            "status": "RUNNING",
            "detail": "Backfill is already running",
        }
    from app.workers.celery_app import celery_app

    queued = celery_app.send_task(task_name)
    return {"queued": True, "task": task_name, "task_id": queued.id, "status": "QUEUED"}


@router.post("/movies/{movie_id}/repair", dependencies=[Depends(require_same_origin)])
def repair_movie(
    movie_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    if not db.get(Movie, movie_id):
        raise HTTPException(404, "Movie not found")
    from app.workers.celery_app import celery_app

    queued = celery_app.send_task("repair.movie", args=[movie_id])
    return {"queued": True, "movie_id": movie_id, "task_id": queued.id}


def _queue_deep_repair(movie: Movie) -> dict:
    from app.workers.celery_app import celery_app

    queued = celery_app.send_task("repair.movie", args=[movie.id])
    return {
        "queued": True,
        "local_movie_id": movie.id,
        "display_id": movie.tmdb_id,
        "task_id": queued.id,
        "workflow": [
            "metadata",
            "people",
            "images",
            "imdb",
            "release-status",
            "eligible-ott",
        ],
    }


@router.post(
    "/deep-search/movies/{tmdb_id}/import", dependencies=[Depends(require_same_origin)]
)
def import_deep_search_movie(
    tmdb_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    """Create one explicit TMDB selection and enqueue the normal repair workflow."""
    if tmdb_id <= 0:
        raise HTTPException(422, "Invalid movie ID")
    limit(request, "deep-search-import", 10, 3600)
    existing = db.query(Movie).filter_by(tmdb_id=tmdb_id).first()
    if existing:
        MovieRequestAutomationService(db).reconcile_for_movie(existing)
        return {
            "created": False,
            "queued": False,
            "status": "already_exists",
            "local_movie_id": existing.id,
            "display_id": existing.tmdb_id,
        }
    if not (settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN):
        raise HTTPException(503, "Live TMDB search is not configured")
    try:
        payload = TMDbMovieService().get_movie(tmdb_id)
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
        if status == 404:
            raise HTTPException(404, "TMDB movie not found") from exc
        raise HTTPException(502, "TMDB is temporarily unavailable") from exc
    if payload.get("id") != tmdb_id or not (
        payload.get("title") or payload.get("original_title")
    ):
        raise HTTPException(502, "TMDB returned an invalid movie record")
    release_date = None
    if payload.get("release_date"):
        try:
            release_date = date.fromisoformat(payload["release_date"][:10])
        except (TypeError, ValueError):
            pass
    movie = Movie(
        tmdb_id=tmdb_id,
        title=payload.get("title") or payload.get("original_title"),
        original_title=payload.get("original_title") or None,
        overview=payload.get("overview") or None,
        release_date=release_date,
        runtime_minutes=payload.get("runtime"),
        poster_path=payload.get("poster_path") or None,
        backdrop_path=payload.get("backdrop_path") or None,
        popularity=payload.get("popularity"),
        vote_average=payload.get("vote_average"),
        vote_count=payload.get("vote_count"),
        original_language=payload.get("original_language") or None,
        adult=bool(payload.get("adult", False)),
        status=payload.get("status") or None,
        tagline=payload.get("tagline") or None,
        budget=payload.get("budget"),
        revenue=payload.get("revenue"),
    )
    db.add(movie)
    try:
        db.flush()
        ReleaseStatusService(db).classify_movie(movie)
        db.commit()
        db.refresh(movie)
    except IntegrityError:
        db.rollback()
        existing = db.query(Movie).filter_by(tmdb_id=tmdb_id).first()
        if existing:
            MovieRequestAutomationService(db).reconcile_for_movie(existing)
            return {
                "created": False,
                "queued": False,
                "status": "already_exists",
                "local_movie_id": existing.id,
                "display_id": existing.tmdb_id,
            }
        raise
    MovieRequestAutomationService(db).reconcile_for_movie(movie)
    return {"created": True, "status": "imported"} | _queue_deep_repair(movie)


@router.post(
    "/deep-search/movies/{tmdb_id}/repair", dependencies=[Depends(require_same_origin)]
)
def repair_deep_search_movie(
    tmdb_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    """Queue non-destructive repair for an existing movie selected by TMDB ID."""
    if tmdb_id <= 0:
        raise HTTPException(422, "Invalid movie ID")
    limit(request, "deep-search-repair", 20, 3600)
    movie = db.query(Movie).filter_by(tmdb_id=tmdb_id).first()
    if not movie:
        raise HTTPException(404, "Movie is not in the local database")
    return {"created": False, "status": "repair_queued"} | _queue_deep_repair(movie)


@router.get("/notifications")
def notifications(
    channel: str | None = None,
    severity: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(NotificationLog)
    if channel:
        query = query.filter(NotificationLog.channel == channel)
    if severity:
        query = query.filter(NotificationLog.severity == severity)
    total = query.count()
    rows = (
        query.order_by(NotificationLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return _pagination(total, page, page_size) | {
        "items": [
            {
                "id": item.id,
                "timestamp": item.created_at,
                "channel": item.channel,
                "severity": item.severity,
                "message": item.message,
                "fingerprint": item.fingerprint,
                "status": "sent" if item.last_notified_at else "failed",
            }
            for item in rows
        ]
    }
