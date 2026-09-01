"""Cookie-authenticated operational administration API."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, exists, func, or_, select
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
from app.models.discovery import MovieDiscoveryCandidate, MovieDiscoveryRun
from app.models.movie_metadata import (
    ExternalId,
    MovieCredit,
    MovieRating,
    MovieTrailer,
    Person,
)
from app.models.genre import movie_genres
from app.models.operations import (
    AdminAuditLog,
    BackfillRecord,
    DataQualityIssue,
    MovieComment,
    MovieRequest,
    NotificationLog,
    OperationState,
    OttEvidence,
    OttSourceRelease,
)
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import (
    OttAvailabilityObservation,
    OttGoldSetCase,
    OttProviderBudgetPeriod,
    OttProviderHealth,
    OttReconciliationDecision,
)
from app.services.image_fallback import ImageFallbackService
from app.services.languages import language_name
from app.services.movie_requests import (
    EMAIL_KINDS,
    MovieRequestAutomationService,
    MovieRequestEmailService,
)
from app.services.ott_source_sync import OttSourceSyncService, SOURCES
from app.services.ott.gold_set import OttGoldSetService
from app.services.ott.provider_controls import OTTApiBudgetManager
from app.services.ott.reconciliation import SOURCE_AUTHORITY, source_type as normalized_source_type
from app.services.operations import OttResearchService, ResearchUsageService
from app.services.roles import ROLE_ALIASES, normalize_role
from app.services.release_status import (
    ELIGIBILITY_LABELS,
    RELEASE_LABELS,
    ReleaseStatusService,
    best_canonical_ott,
    site_date,
)
from app.services.tmdb.movie_service import TMDbMovieService
from app.services.movie_discovery import MovieDiscoveryService, next_regular_discovery

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
    ott_release_date: date | None = None
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


class SourceReleaseAction(BaseModel):
    action: str = Field(pattern="^(match|ignore|tv_series|duplicate|research)$")
    movie_id: int | None = Field(default=None, ge=1)


class DiscoveryCandidateAction(BaseModel):
    action: str = Field(pattern="^(ignore|duplicate|wrong_language|tv_series|match_existing)$")
    movie_id: int | None = Field(default=None, ge=1)


class GoldSetUpdate(BaseModel):
    expected_platform: str | None = Field(default=None, max_length=100)
    expected_release_date: date | None = None
    expected_availability_type: str | None = Field(default=None, max_length=30)
    expected_state: str = Field(default="UNKNOWN", pattern="^(UNKNOWN|PLATFORM_ONLY|UPCOMING_CONFIRMED|RELEASED_CONFIRMED|NOT_FOUND)$")
    source_url: str | None = Field(default=None, max_length=1000, pattern=r"^https://")
    notes: str | None = Field(default=None, max_length=2000)


def _pagination(total: int, page: int, page_size: int):
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


def _audit(
    db: Session,
    action: str,
    target_type: str,
    target_id: object | None = None,
    summary: str | None = None,
) -> AdminAuditLog:
    """Attach a bounded, secret-free administrator event to the transaction."""
    item = AdminAuditLog(
        action=action[:80],
        target_type=target_type[:40],
        target_id=str(target_id)[:100] if target_id is not None else None,
        summary=(summary or "")[:1000] or None,
    )
    db.add(item)
    return item


def _email_health(db: Session) -> dict:
    fields = {
        "request_confirmation": MovieRequest.confirmation_email_status,
        "admin_notification": MovieRequest.admin_notification_email_status,
        "completion": MovieRequest.completion_email_status,
    }
    metrics = {}
    for label, field in fields.items():
        metrics[f"{label}_sent"] = db.query(MovieRequest).filter(field == "SENT").count()
        metrics[f"{label}_failed"] = db.query(MovieRequest).filter(field == "FAILED").count()
        metrics[f"{label}_pending"] = db.query(MovieRequest).filter(field == "PENDING").count()
    last_values = db.query(
        func.max(MovieRequest.confirmation_email_sent_at),
        func.max(MovieRequest.admin_notification_email_sent_at),
        func.max(MovieRequest.completion_email_sent_at),
    ).one()
    last_sent = max((value for value in last_values if value), default=None)
    return {
        "smtp_configured": bool(settings.SMTP_HOST and settings.SMTP_FROM),
        "last_successful_email": last_sent,
        "failed": sum(value for key, value in metrics.items() if key.endswith("_failed")),
        "pending": sum(value for key, value in metrics.items() if key.endswith("_pending")),
        **metrics,
    }


def _source_health(db: Session) -> list[dict]:
    states = {item.name: item for item in db.query(OperationState).all()}

    def state_snapshot(name: str):
        state = states.get(name)
        return {
            "status": state.status if state else "IDLE",
            "last_success": state.last_success_at if state else None,
            "last_error": state.last_error if state else None,
        }

    tmdb_state = states.get("movies.discovery") or states.get("tmdb.metadata_backfill") or states.get("tmdb.incremental_sync")
    tavily_usage = ResearchUsageService(db).monthly_snapshot()
    email = _email_health(db)
    sources = [
        {
            "source": "tmdb",
            "label": "TMDB",
            "enabled": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
            "healthy": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN) and not (tmdb_state and tmdb_state.status == "FAILED"),
            **state_snapshot(tmdb_state.name if tmdb_state else "movies.discovery"),
        },
        {
            "source": "tavily",
            "label": "Tavily",
            "enabled": bool(settings.TAVILY_API_KEY or (settings.OTT_RESEARCH_PROVIDER.lower() == "tavily" and settings.OTT_SEARCH_API_KEY)),
            "healthy": bool(settings.TAVILY_API_KEY or (settings.OTT_RESEARCH_PROVIDER.lower() == "tavily" and settings.OTT_SEARCH_API_KEY)),
            **state_snapshot("operations.ott_research"),
            "usage": tavily_usage,
        },
        {
            "source": "smtp",
            "label": "SMTP",
            "enabled": bool(settings.SMTP_HOST and settings.SMTP_FROM),
            "healthy": bool(settings.SMTP_HOST and settings.SMTP_FROM) and email["failed"] == 0,
            "status": "CONFIGURED" if settings.SMTP_HOST and settings.SMTP_FROM else "NOT_CONFIGURED",
            "last_success": email["last_successful_email"],
            "last_error": None,
        },
        {
            "source": "youtube",
            "label": "YouTube trailer metadata",
            "enabled": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
            "healthy": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
            **state_snapshot("tmdb.trailer_backfill"),
        },
    ]
    for name in ("ottplay", "justwatch"):
        snapshot = OttSourceSyncService(db, name).snapshot()
        sources.append(
            {
                "source": name,
                "label": "OTTplay" if name == "ottplay" else "JustWatch",
                "enabled": snapshot["enabled"],
                "healthy": snapshot["enabled"] and snapshot["status"] not in {"FAILED", "BLOCKED"},
                **snapshot,
            }
        )
    budget = OTTApiBudgetManager(db)
    for name, label, enabled in (
        ("tmdb_justwatch", "TMDB / JustWatch India", bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN)),
        ("streaming_availability", "Streaming Availability", bool(settings.STREAMING_AVAILABILITY_ENABLED and settings.STREAMING_AVAILABILITY_API_KEY)),
        ("watchmode", "Watchmode", bool(settings.WATCHMODE_ENABLED and settings.WATCHMODE_API_KEY)),
    ):
        health = db.query(OttProviderHealth).filter_by(provider=name).first()
        sources.append(
            {
                "source": name,
                "label": label,
                "enabled": enabled,
                "configured": enabled,
                "healthy": bool(enabled and (not health or health.status == "HEALTHY")),
                "status": health.status if health else ("IDLE" if enabled else "DISABLED"),
                "last_check": health.updated_at if health else None,
                "last_success": health.last_success_at if health else None,
                "last_failure": health.last_failure_at if health else None,
                "last_error": health.last_error if health else None,
                "next_run": health.circuit_open_until if health else None,
                "stats": {
                    "requests": health.request_count if health else 0,
                    "successes": health.success_count if health else 0,
                    "errors": health.error_count if health else 0,
                    "matches": health.match_count if health else 0,
                },
                "budget": budget.snapshot(name),
            }
        )
    return sources


def _health_summary(db: Session) -> dict:
    total = db.query(Movie).count()
    cast_exists = exists().where(and_(MovieCredit.movie_id == Movie.id, MovieCredit.credit_type == "cast"))
    crew_role = lambda roles: exists().where(
        and_(
            MovieCredit.movie_id == Movie.id,
            MovieCredit.credit_type == "crew",
            func.lower(MovieCredit.job).in_(roles),
        )
    )
    with_ott = select(OttAvailability.movie_id).where(OttAvailability.country == "IN")
    with_ott_date = select(OttAvailability.movie_id).where(
        OttAvailability.country == "IN", OttAvailability.ott_release_date.is_not(None)
    )
    with_trailer = select(MovieTrailer.movie_id)
    with_imdb = select(ExternalId.movie_id).where(func.lower(ExternalId.provider) == "imdb")
    with_rating = select(MovieRating.movie_id).where(
        func.lower(MovieRating.source) == "imdb", MovieRating.rating.is_not(None)
    )
    open_issue = DataQualityIssue.resolved_at.is_(None)
    jobs = dict(db.query(OperationState.status, func.count(OperationState.id)).group_by(OperationState.status).all())
    request_counts = dict(db.query(MovieRequest.status, func.count(MovieRequest.id)).group_by(MovieRequest.status).all())
    now = datetime.now(timezone.utc)
    return {
        "movies": {
            "total": total,
            "missing_title": db.query(Movie).filter(or_(Movie.title.is_(None), Movie.title == "")).count(),
            "missing_release_date": db.query(Movie).filter(Movie.release_date.is_(None)).count(),
            "missing_language": db.query(Movie).filter(or_(Movie.original_language.is_(None), Movie.original_language == "")).count(),
            "missing_genre": db.query(Movie).filter(~exists().where(movie_genres.c.movie_id == Movie.id)).count(),
            "missing_runtime": db.query(Movie).filter(Movie.runtime_minutes.is_(None)).count(),
        },
        "identifiers": {
            "missing_tmdb": db.query(Movie).filter(Movie.tmdb_id.is_(None)).count(),
            "missing_imdb": db.query(Movie).filter(~Movie.id.in_(with_imdb)).count(),
        },
        "credits": {
            "missing_cast": db.query(Movie).filter(~cast_exists).count(),
            "missing_director": db.query(Movie).filter(~crew_role(ROLE_ALIASES["director"])).count(),
            "missing_cinematography": db.query(Movie).filter(~crew_role(ROLE_ALIASES["cinematography"])).count(),
            "missing_writer": db.query(Movie).filter(~crew_role(ROLE_ALIASES["writer"])).count(),
            "missing_composer": db.query(Movie).filter(~crew_role(ROLE_ALIASES["composer"])).count(),
            "people_missing_profile": db.query(Person).filter(or_(Person.profile_path.is_(None), Person.profile_path == "")).count(),
            "people_missing_biography": db.query(Person).filter(or_(Person.biography.is_(None), Person.biography == "")).count(),
        },
        "images": {
            "missing_poster": db.query(Movie).filter(or_(Movie.poster_path.is_(None), Movie.poster_path == "")).count(),
            "broken_poster": db.query(DataQualityIssue).filter(open_issue, DataQualityIssue.issue_type == "broken_poster").count(),
            "missing_backdrop": db.query(Movie).filter(or_(Movie.backdrop_path.is_(None), Movie.backdrop_path == "")).count(),
            "missing_logo": db.query(DataQualityIssue).filter(open_issue, DataQualityIssue.issue_type == "missing_logo").count(),
        },
        "ott": {
            "missing_platform": db.query(Movie).filter(~Movie.id.in_(with_ott)).count(),
            "missing_date": db.query(Movie).filter(~Movie.id.in_(with_ott_date)).count(),
            "platform_only": db.query(Movie).filter(Movie.id.in_(with_ott), ~Movie.id.in_(with_ott_date)).count(),
            "confirmed": db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.verification_status == "CONFIRMED").scalar() or 0,
            "conflicting": db.query(func.count(func.distinct(OttEvidence.movie_id))).filter(OttEvidence.status == "CONFLICTING").scalar() or 0,
            "needs_review": db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.verification_status == "NEEDS_REVIEW").scalar() or 0,
        },
        "trailers": {
            "available": db.query(func.count(func.distinct(MovieTrailer.movie_id))).scalar() or 0,
            "missing": db.query(Movie).filter(~Movie.id.in_(with_trailer)).count(),
            "invalid": db.query(MovieTrailer).filter(func.length(MovieTrailer.video_key) != 11).count(),
        },
        "ratings": {
            "imdb_available": db.query(func.count(func.distinct(MovieRating.movie_id))).filter(func.lower(MovieRating.source) == "imdb", MovieRating.rating.is_not(None)).scalar() or 0,
            "imdb_missing": db.query(Movie).filter(~Movie.id.in_(with_rating)).count(),
        },
        "requests": {
            "pending": request_counts.get("PENDING", 0),
            "reviewing": request_counts.get("REVIEWING", 0),
            "overdue": db.query(MovieRequest).filter(MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]), MovieRequest.created_at < now - timedelta(hours=48)).count(),
        },
        "comments": {"pending": db.query(MovieComment).filter_by(status="PENDING").count()},
        "jobs": {
            "failed": jobs.get("FAILED", 0),
            "running": jobs.get("RUNNING", 0),
            "queued": jobs.get("QUEUED", 0),
        },
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
    now = datetime.now(timezone.utc)
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    open_filter = DataQualityIssue.resolved_at.is_(None)
    recent_notifications = (
        db.query(NotificationLog)
        .order_by(NotificationLog.created_at.desc())
        .limit(10)
        .all()
    )
    jobs = db.query(OperationState).order_by(OperationState.name).all()
    status_counts = dict(
        db.query(MovieRequest.status, func.count(MovieRequest.id))
        .group_by(MovieRequest.status)
        .all()
    )
    total_movies = db.query(Movie).count()
    confirmed_ott = (
        db.query(func.count(func.distinct(OttAvailability.movie_id)))
        .filter(OttAvailability.verification_status == "CONFIRMED")
        .scalar()
        or 0
    )
    upcoming_ott = (
        db.query(func.count(func.distinct(OttAvailability.movie_id)))
        .filter(
            OttAvailability.verification_status == "CONFIRMED",
            OttAvailability.ott_release_date > today,
        )
        .scalar()
        or 0
    )
    with_rating = select(MovieRating.movie_id).where(
        func.lower(MovieRating.source) == "imdb", MovieRating.rating.is_not(None)
    )
    with_trailer = select(MovieTrailer.movie_id)
    active_request = MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"])
    emails = _email_health(db)
    site_zone = ZoneInfo(settings.SITE_TIMEZONE)
    site_today = now.astimezone(site_zone).date()
    site_day_start = datetime.combine(site_today, time.min, site_zone).astimezone(timezone.utc)
    last_discovery = db.query(MovieDiscoveryRun).order_by(MovieDiscoveryRun.started_at.desc()).first()
    last_discovery_success = (
        db.query(MovieDiscoveryRun)
        .filter(MovieDiscoveryRun.status == "COMPLETE")
        .order_by(MovieDiscoveryRun.completed_at.desc())
        .first()
    )
    discovery_today = db.query(
        func.coalesce(func.sum(MovieDiscoveryRun.candidates_discovered), 0),
        func.coalesce(func.sum(MovieDiscoveryRun.new_movies_imported), 0),
        func.coalesce(func.sum(MovieDiscoveryRun.needs_review), 0),
        func.coalesce(func.sum(MovieDiscoveryRun.failed), 0),
    ).filter(MovieDiscoveryRun.started_at >= site_day_start).one()
    discovery_slots = {
        slot: (
            db.query(MovieDiscoveryRun)
            .filter(MovieDiscoveryRun.run_type == "REGULAR", MovieDiscoveryRun.slot == slot)
            .order_by(MovieDiscoveryRun.started_at.desc())
            .first()
        )
        for slot in ("MORNING", "EVENING")
    }
    discovery_stale = not last_discovery_success or last_discovery_success.completed_at < now - timedelta(hours=26)
    failed_jobs = db.query(OperationState).filter(OperationState.status == "FAILED").count()
    recent_activity = [
        {
            "id": f"audit-{item.id}",
            "timestamp": item.created_at,
            "event": item.action.replace("_", " ").title(),
            "target": f"{item.target_type} {item.target_id or ''}".strip(),
            "status": item.summary,
        }
        for item in db.query(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(12)
        .all()
    ]
    if len(recent_activity) < 12:
        recent_activity.extend(
            {
                "id": f"request-{item.id}",
                "timestamp": item.created_at,
                "event": "Movie request submitted",
                "target": item.request_id,
                "status": item.status,
            }
            for item in db.query(MovieRequest)
            .order_by(MovieRequest.created_at.desc())
            .limit(12 - len(recent_activity))
            .all()
        )
    summary = {
        "total_movies": total_movies,
        "movies_added_today": db.query(Movie).filter(Movie.created_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)).count(),
        "movies_added_this_week": db.query(Movie).filter(Movie.created_at >= datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc)).count(),
        "pending_requests": status_counts.get("PENDING", 0),
        "reviewing_requests": status_counts.get("REVIEWING", 0),
        "added_requests": status_counts.get("ADDED", 0),
        "pending_comments": db.query(MovieComment).filter(MovieComment.status == "PENDING").count(),
        "ott_confirmed": confirmed_ott,
        "upcoming_ott": upcoming_ott,
        "missing_ott": max(0, total_movies - (db.query(func.count(func.distinct(OttAvailability.movie_id))).scalar() or 0)),
        "ott_needs_review": db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.verification_status == "NEEDS_REVIEW").scalar() or 0,
        "movies_missing_images": db.query(Movie).filter(or_(Movie.poster_path.is_(None), Movie.poster_path == "", Movie.backdrop_path.is_(None), Movie.backdrop_path == "")).count(),
        "movies_missing_imdb_rating": db.query(Movie).filter(~Movie.id.in_(with_rating)).count(),
        "movies_missing_trailer": db.query(Movie).filter(~Movie.id.in_(with_trailer)).count(),
        "failed_jobs": failed_jobs,
        "discovered_today": int(discovery_today[0]),
        "discovery_imported_today": int(discovery_today[1]),
        "discovery_review_today": int(discovery_today[2]),
        "discovery_failures_today": int(discovery_today[3]),
        "requests_over_36h": db.query(MovieRequest).filter(active_request, MovieRequest.created_at < now - timedelta(hours=36)).count(),
        "requests_over_48h": db.query(MovieRequest).filter(active_request, MovieRequest.created_at < now - timedelta(hours=48)).count(),
        "failed_emails": emails["failed"],
    }
    alerts = [
        {"label": f"{summary['pending_requests']} movie requests pending", "severity": "warning", "href": "/admin/requests?status=PENDING"},
        {"label": f"{summary['requests_over_48h']} requests overdue more than 48 hours", "severity": "critical", "href": "/admin/requests?age=48"},
        {"label": f"{summary['ott_needs_review']} OTT records need review", "severity": "warning", "href": "/admin/ott-research?status=NEEDS_REVIEW"},
        {"label": f"{summary['failed_emails']} request emails failed", "severity": "critical", "href": "/admin/requests?email_status=FAILED"},
        {"label": f"{summary['movies_missing_trailer']} movies are missing a trailer", "severity": "neutral", "href": "/admin/movies?trailer=missing"},
        {"label": f"{failed_jobs} background jobs failed", "severity": "critical", "href": "/admin/jobs?status=FAILED"},
        {"label": "Movie discovery is stale: two scheduled opportunities have passed", "severity": "critical", "href": "/admin/discovery"} if discovery_stale else {"label": "", "severity": "neutral", "href": "/admin/discovery"},
        {"label": f"Latest movie discovery run is {last_discovery.status.lower()}", "severity": "warning", "href": "/admin/discovery"} if last_discovery and last_discovery.status != "COMPLETE" else {"label": "", "severity": "neutral", "href": "/admin/discovery"},
    ]
    return summary | {
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
        "alerts": [item for item in alerts if item["label"] and not item["label"].startswith("0 ")],
        "discovery": {
            "timezone": settings.SITE_TIMEZONE,
            "last_run": MovieDiscoveryService.serialize_run(last_discovery) if last_discovery else None,
            "next_run": next_regular_discovery(now),
            "stale": discovery_stale,
            "today": {
                "discovered": int(discovery_today[0]),
                "imported": int(discovery_today[1]),
                "needs_review": int(discovery_today[2]),
                "failed": int(discovery_today[3]),
            },
            "slots": {
                slot.lower(): MovieDiscoveryService.serialize_run(item) if item else None
                for slot, item in discovery_slots.items()
            },
        },
        "recent_activity": sorted(recent_activity, key=lambda item: item["timestamp"], reverse=True)[:12],
        "sources": _source_health(db),
        "email": emails,
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


@router.get("/discovery")
def discovery(
    status: str | None = None,
    language: str | None = Query(default=None, pattern="^(ml|ta|te|hi|kn)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    allowed = {"DISCOVERED", "EXISTING", "IMPORTED", "NEEDS_REVIEW", "FAILED", "FILTERED", "IGNORED", "DUPLICATE", "WRONG_LANGUAGE", "TV_SERIES"}
    query = db.query(MovieDiscoveryCandidate)
    if status:
        if status not in allowed:
            raise HTTPException(422, "Unknown discovery status")
        query = query.filter(MovieDiscoveryCandidate.status == status)
    if language:
        query = query.filter(MovieDiscoveryCandidate.language == language)
    total = query.count()
    candidates = query.order_by(
        MovieDiscoveryCandidate.last_seen_at.desc(), MovieDiscoveryCandidate.id.desc()
    ).offset((page - 1) * page_size).limit(page_size).all()
    runs = db.query(MovieDiscoveryRun).order_by(MovieDiscoveryRun.started_at.desc()).limit(20).all()
    counts = dict(
        db.query(MovieDiscoveryCandidate.status, func.count(MovieDiscoveryCandidate.id))
        .group_by(MovieDiscoveryCandidate.status)
        .all()
    )
    return {
        **_pagination(total, page, page_size),
        "timezone": settings.SITE_TIMEZONE,
        "next_run": next_regular_discovery(),
        "counts": counts,
        "runs": [MovieDiscoveryService.serialize_run(item) for item in runs],
        "items": [
            {
                "id": item.id,
                "source": item.source,
                "external_key": item.external_key,
                "tmdb_id": item.tmdb_id,
                "imdb_id": item.imdb_id,
                "title": item.title,
                "original_title": item.original_title,
                "language": item.language,
                "release_date": item.release_date,
                "status": item.status,
                "matched_movie_id": item.matched_movie_id,
                "match_confidence": item.match_confidence,
                "match_reason": item.match_reason,
                "first_discovered_at": item.first_discovered_at,
                "last_seen_at": item.last_seen_at,
                "last_error": item.last_error,
            }
            for item in candidates
        ],
    }


@router.patch("/discovery/{candidate_id}", dependencies=[Depends(require_same_origin)])
def update_discovery_candidate(
    candidate_id: int,
    payload: DiscoveryCandidateAction,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    candidate = db.get(MovieDiscoveryCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Discovery candidate not found")
    statuses = {
        "ignore": "IGNORED",
        "duplicate": "DUPLICATE",
        "wrong_language": "WRONG_LANGUAGE",
        "tv_series": "TV_SERIES",
        "match_existing": "EXISTING",
    }
    if payload.action == "match_existing":
        if not payload.movie_id or not db.get(Movie, payload.movie_id):
            raise HTTPException(422, "A valid local movie ID is required")
        candidate.matched_movie_id = payload.movie_id
        candidate.match_confidence = 100
        candidate.match_reason = "Administrator matched existing movie"
    candidate.status = statuses[payload.action]
    _audit(db, f"discovery_{payload.action}", "movie_discovery_candidate", candidate.id, candidate.title)
    db.commit()
    return {"id": candidate.id, "status": candidate.status, "matched_movie_id": candidate.matched_movie_id}


@router.get("/requests")
def requests(
    search: str | None = None,
    status: str | None = None,
    email_status: str | None = None,
    local: str | None = None,
    age: int | None = Query(default=None, ge=24, le=48),
    requested_today: bool = False,
    sort: str = Query("newest", pattern="^(newest|oldest|highest_age|recently_updated)$"),
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
                MovieRequest.imdb_id.ilike(term),
                request_id_match,
            )
        )
    if email_status:
        if email_status not in {"FAILED", "PENDING", "SENT", "NOT_CONFIGURED"}:
            raise HTTPException(422, "Unknown email status")
        query = query.filter(
            or_(
                MovieRequest.confirmation_email_status == email_status,
                MovieRequest.admin_notification_email_status == email_status,
                MovieRequest.completion_email_status == email_status,
                MovieRequest.rejection_email_status == email_status,
            )
        )
    if local == "exists":
        query = query.filter(MovieRequest.local_movie_id.is_not(None))
    elif local == "missing":
        query = query.filter(MovieRequest.local_movie_id.is_(None))
    elif local not in {None, ""}:
        raise HTTPException(422, "Unknown local movie filter")
    now = datetime.now(timezone.utc)
    if age:
        query = query.filter(MovieRequest.created_at < now - timedelta(hours=age))
    if requested_today:
        query = query.filter(MovieRequest.created_at >= datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc))
    total = query.count()
    order = {
        "newest": (MovieRequest.created_at.desc(),),
        "oldest": (MovieRequest.created_at.asc(),),
        "highest_age": (MovieRequest.created_at.asc(),),
        "recently_updated": (MovieRequest.updated_at.desc(),),
    }[sort]
    rows = (
        query.order_by(*order, MovieRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    counters = dict(
        db.query(MovieRequest.status, func.count(MovieRequest.id))
        .group_by(MovieRequest.status)
        .all()
    )
    return _pagination(total, page, page_size) | {
        "counters": {"ALL": sum(counters.values())} | {key: counters.get(key, 0) for key in sorted(REQUEST_STATUSES)},
        "items": [_request(item) for item in rows],
    }


def _request(item: MovieRequest, db: Session | None = None, rich: bool = False):
    now = datetime.now(timezone.utc)
    created = (
        item.created_at
        if item.created_at.tzinfo
        else item.created_at.replace(tzinfo=timezone.utc)
    )
    age_seconds = max(0, int((now - created).total_seconds()))
    target_at = created + timedelta(hours=48)
    age_hours = age_seconds / 3600
    result = {
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
        "sla": "OVERDUE" if age_hours >= 48 else "URGENT" if age_hours >= 36 else "ATTENTION" if age_hours >= 24 else "NORMAL",
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
    if not (db and rich):
        return result
    movie = None
    if item.local_movie_id:
        movie = (
            db.query(Movie)
            .options(
                selectinload(Movie.ott_availabilities),
                selectinload(Movie.trailers),
                selectinload(Movie.ratings),
                selectinload(Movie.external_ids),
                selectinload(Movie.credits).selectinload(MovieCredit.person),
            )
            .filter(Movie.id == item.local_movie_id)
            .first()
        )
    if not movie and item.external_movie_id:
        movie = (
            db.query(Movie)
            .options(
                selectinload(Movie.ott_availabilities),
                selectinload(Movie.trailers),
                selectinload(Movie.ratings),
                selectinload(Movie.external_ids),
                selectinload(Movie.credits).selectinload(MovieCredit.person),
            )
            .filter(Movie.tmdb_id == item.external_movie_id)
            .first()
        )
    if not movie:
        result["local"] = {"exists": False}
        result["data_completeness"] = {
            "poster": bool(item.poster_path),
            "tmdb": bool(item.external_movie_id),
            "imdb": bool(item.imdb_id),
            "cast": False,
            "director": bool(item.director),
            "theatrical_date": bool(item.verified_release_date),
            "ott_date": False,
            "ott_platform": False,
            "trailer": False,
            "imdb_rating": False,
        }
        result["ott"] = {"status": "NOT_LOCAL", "sources": []}
        result["trailer"] = {"available": False}
        return result
    canonical = best_canonical_ott(movie)
    trailer = next((row for row in movie.trailers if row.is_primary), movie.trailers[0] if movie.trailers else None)
    imdb_rating = next((row for row in movie.ratings if row.source.lower() == "imdb" and row.rating is not None), None)
    imdb_id = next((row.external_id for row in movie.external_ids if row.provider.lower() == "imdb"), item.imdb_id)
    cast = [credit.person.name for credit in sorted(movie.credits, key=lambda x: x.cast_order if x.cast_order is not None else 9999) if credit.credit_type == "cast"][:12]
    directors = [credit.person.name for credit in movie.credits if normalize_role(credit.job or credit.department) == "director"]
    latest_research = (
        db.query(OttEvidence)
        .filter(OttEvidence.movie_id == movie.id, OttEvidence.source_url.is_(None))
        .order_by(OttEvidence.updated_at.desc(), OttEvidence.id.desc())
        .first()
    )
    sources = (
        db.query(OttEvidence)
        .filter(OttEvidence.movie_id == movie.id, OttEvidence.source_url.is_not(None), OttEvidence.rejected_at.is_(None))
        .order_by(OttEvidence.confidence.desc(), OttEvidence.created_at.desc())
        .all()
    )
    decision = (
        db.query(OttReconciliationDecision)
        .filter_by(movie_id=movie.id, country="IN", is_current=True)
        .order_by(OttReconciliationDecision.health_score.desc(), OttReconciliationDecision.id.desc())
        .first()
    )
    observation_count = db.query(OttAvailabilityObservation).filter_by(movie_id=movie.id, country="IN").count()
    last_observation = db.query(func.max(OttAvailabilityObservation.observed_at)).filter_by(movie_id=movie.id, country="IN").scalar()
    result["local_movie_id"] = movie.id
    result["local"] = {
        "exists": True,
        "id": movie.id,
        "metadata_status": "COMPLETE" if movie.title and movie.release_date and movie.original_language and cast and directors else "INCOMPLETE",
        "runtime": movie.runtime_minutes,
        "cast": cast,
        "directors": list(dict.fromkeys(directors)),
        "added_at": movie.created_at,
        "updated_at": movie.updated_at,
    }
    result["ott"] = {
        "platform": canonical.provider if canonical else None,
        "release_date": canonical.ott_release_date if canonical and canonical.verification_status == "CONFIRMED" else None,
        "confidence": canonical.confidence if canonical else 0,
        "verification_status": canonical.verification_status if canonical else "UNKNOWN",
        "status": latest_research.status if latest_research else "NOT_QUEUED",
        "last_check": latest_research.last_checked if latest_research else None,
        "release_state": decision.state if decision else (canonical.release_state if canonical else "UNKNOWN"),
        "health_score": decision.health_score if decision else (canonical.health_score if canonical else 0),
        "reconciliation_reason": decision.reason if decision else None,
        "observation_count": observation_count,
        "last_observation": last_observation,
        "sources": [
            {"id": row.id, "name": row.source_name or row.source_type, "url": row.source_url, "platform": row.platform, "date": row.release_date, "confidence": row.confidence}
            for row in sources
        ],
    }
    result["trailer"] = {
        "available": bool(trailer),
        "provider": trailer.provider if trailer else None,
        "video_key": trailer.video_key if trailer else None,
        "name": trailer.name if trailer else None,
        "official": trailer.official if trailer else False,
    }
    result["imdb_id"] = imdb_id
    result["data_completeness"] = {
        "poster": bool(movie.poster_path),
        "tmdb": bool(movie.tmdb_id),
        "imdb": bool(imdb_id),
        "cast": bool(cast),
        "director": bool(directors),
        "theatrical_date": bool(movie.theatrical_release_date or movie.release_date),
        "ott_date": bool(canonical and canonical.ott_release_date and canonical.verification_status == "CONFIRMED"),
        "ott_platform": bool(canonical and canonical.provider),
        "trailer": bool(trailer),
        "imdb_rating": bool(imdb_rating),
    }
    result["imdb_rating"] = imdb_rating.rating if imdb_rating else None
    return result


@router.get("/requests/{request_id}")
def request_detail(
    request_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    item = db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item:
        raise HTTPException(404, "Request not found")
    return _request(item, db, rich=True)


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
        _audit(db, "request_status_changed", "movie_request", request_id, "Status changed to ADDED")
        db.commit()
        return _request(item, db, rich=True)
    item.status = payload.status
    _audit(db, "request_status_changed", "movie_request", request_id, f"Status changed to {payload.status}")
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
    _audit(db, "request_email_retried", "movie_request", request_id, f"{kind} email: {result.get('status', 'attempted')}")
    db.commit()
    return result | {"request": _request(item)}


@router.get("/comments")
def comments(
    status: str | None = None,
    today: bool = False,
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
    if today:
        query = query.filter(MovieComment.created_at >= datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc))
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
                "poster_path": movie.poster_path,
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
    _audit(db, "comment_moderated", "movie_comment", item.id, f"Status changed to {item.status}")
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
    _audit(db, "comment_deleted", "movie_comment", item.id, f"Deleted comment on movie {item.movie_id}")
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
        "summary": _health_summary(db),
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
                "platform_confidence": canonical.platform_confidence,
                "date_confidence": canonical.date_confidence,
                "release_state": canonical.release_state,
                "health_score": canonical.health_score,
                "original_premiere": canonical.is_original_premiere,
                "observed_available_from": canonical.observed_available_from,
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
                "fact_type": item.fact_type,
                "availability_type": item.availability_type,
                "movie_match_confidence": item.movie_match_confidence,
                "platform_confidence": item.platform_confidence,
                "date_confidence": item.date_confidence,
                "observed_at": item.observed_at,
                "verification_method": item.verification_method,
                "superseded_by_id": item.superseded_by_id,
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
        "decisions": [
            {"id": item.id, "state": item.state, "platform": item.platform, "release_date": item.release_date, "availability_type": item.availability_type, "platform_confidence": item.platform_confidence, "date_confidence": item.date_confidence, "movie_match_confidence": item.movie_match_confidence, "health_score": item.health_score, "reason": item.reason, "supporting_evidence_ids": item.supporting_evidence_ids, "conflicting_evidence_ids": item.conflicting_evidence_ids, "decided_at": item.decided_at}
            for item in db.query(OttReconciliationDecision).filter_by(movie_id=movie_id, country="IN", is_current=True).order_by(OttReconciliationDecision.id).all()
        ],
        "observations": [
            {"id": item.id, "provider": item.provider, "availability_type": item.availability_type, "available": item.available, "source_type": item.source_type, "observed_at": item.observed_at, "source_url": item.source_url}
            for item in db.query(OttAvailabilityObservation).filter_by(movie_id=movie_id, country="IN").order_by(OttAvailabilityObservation.observed_at.desc()).limit(20).all()
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
    _audit(db, "ott_manually_confirmed", "movie", movie_id, f"{payload.platform} on {payload.ott_release_date}")
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
    _audit(db, f"ott_evidence_{payload.action}ed", "ott_evidence", evidence_id, payload.reason)
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
    _audit(db, "ott_research_action", "ott_evidence", evidence_id, payload.action)
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
    _audit(db, "job_started", "background_job", task_name, f"Checkpoint-preserving {operation} run queued")
    db.commit()
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
    _audit(db, "movie_repair_queued", "movie", movie_id)
    db.commit()
    return {"queued": True, "movie_id": movie_id, "task_id": queued.id}


@router.post("/movies/{movie_id}/research-ott", dependencies=[Depends(require_same_origin)])
def research_movie_ott(
    movie_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
    ReleaseStatusService(db).classify_movie(movie)
    queued = service.queue_movie(movie_id)
    latest = (
        db.query(OttEvidence)
        .filter(OttEvidence.movie_id == movie_id, OttEvidence.source_url.is_(None))
        .order_by(OttEvidence.id.desc())
        .first()
    )
    if latest and latest.status not in {"RESEARCHING"}:
        latest.status = "QUEUED"
        latest.next_check = datetime.now(timezone.utc)
        queued = True
    _audit(db, "ott_research_queued", "movie", movie_id)
    db.commit()
    if not queued:
        raise HTTPException(409, "Movie is not currently eligible for OTT research")
    return {"queued": True, "movie_id": movie_id, "status": latest.status if latest else "QUEUED"}


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
        _audit(db, "movie_import_checked", "movie", existing.id, "Movie already existed locally")
        db.commit()
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
    _audit(db, "movie_imported", "movie", movie.id, f"TMDB {tmdb_id}")
    db.commit()
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
    result = {"created": False, "status": "repair_queued"} | _queue_deep_repair(movie)
    _audit(db, "movie_repair_queued", "movie", movie.id, f"TMDB {tmdb_id}")
    db.commit()
    return result


@router.get("/movies")
def admin_movies(
    search: str | None = None,
    language: str | None = None,
    year: int | None = Query(default=None, ge=1888, le=2100),
    platform: str | None = None,
    ott: str | None = None,
    trailer: str | None = None,
    imdb: str | None = None,
    poster: str | None = None,
    metadata: str | None = None,
    sort: str = Query("updated", pattern="^(updated|newest|oldest|title|year)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    """Server-side catalogue operations; no full-database browser payloads."""
    query = db.query(Movie)
    with_ott = select(OttAvailability.movie_id)
    confirmed_ott = select(OttAvailability.movie_id).where(
        OttAvailability.verification_status == "CONFIRMED"
    )
    with_trailer = select(MovieTrailer.movie_id)
    with_imdb = select(ExternalId.movie_id).where(func.lower(ExternalId.provider) == "imdb")
    if search:
        term = f"%{search.strip()}%"
        conditions = [Movie.title.ilike(term), Movie.original_title.ilike(term)]
        if search.strip().isdigit():
            conditions.extend([Movie.id == int(search), Movie.tmdb_id == int(search)])
        conditions.append(
            Movie.id.in_(select(ExternalId.movie_id).where(ExternalId.external_id.ilike(term)))
        )
        query = query.filter(or_(*conditions))
    if language:
        query = query.filter(Movie.original_language == language.lower())
    if year:
        query = query.filter(func.extract("year", Movie.release_date) == year)
    if platform:
        query = query.filter(
            Movie.id.in_(select(OttAvailability.movie_id).where(OttAvailability.provider.ilike(f"%{platform.strip()}%")))
        )
    if ott == "confirmed":
        query = query.filter(Movie.id.in_(confirmed_ott))
    elif ott == "missing":
        query = query.filter(~Movie.id.in_(with_ott))
    elif ott == "needs_review":
        query = query.filter(Movie.id.in_(select(OttAvailability.movie_id).where(OttAvailability.verification_status == "NEEDS_REVIEW")))
    if trailer == "missing":
        query = query.filter(~Movie.id.in_(with_trailer))
    if imdb == "missing":
        query = query.filter(~Movie.id.in_(with_imdb))
    if poster == "missing":
        query = query.filter(or_(Movie.poster_path.is_(None), Movie.poster_path == ""))
    if metadata == "incomplete":
        query = query.filter(
            or_(
                Movie.release_date.is_(None),
                Movie.original_language.is_(None),
                Movie.runtime_minutes.is_(None),
                ~exists().where(MovieCredit.movie_id == Movie.id),
            )
        )
    total = query.count()
    ordering = {
        "updated": (Movie.updated_at.desc(), Movie.id.desc()),
        "newest": (Movie.created_at.desc(), Movie.id.desc()),
        "oldest": (Movie.created_at.asc(), Movie.id.asc()),
        "title": (Movie.title.asc(), Movie.id.asc()),
        "year": (Movie.release_date.desc().nullslast(), Movie.id.desc()),
    }[sort]
    rows = (
        query.options(
            selectinload(Movie.ott_availabilities),
            selectinload(Movie.trailers),
            selectinload(Movie.ratings),
            selectinload(Movie.external_ids),
            selectinload(Movie.credits),
        )
        .order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for movie in rows:
        canonical = best_canonical_ott(movie)
        imdb_id = next((row.external_id for row in movie.external_ids if row.provider.lower() == "imdb"), None)
        rating = next((row for row in movie.ratings if row.source.lower() == "imdb" and row.rating is not None), None)
        trailer_row = next((row for row in movie.trailers if row.is_primary), movie.trailers[0] if movie.trailers else None)
        missing = [
            label
            for label, present in (
                ("release date", movie.release_date),
                ("language", movie.original_language),
                ("runtime", movie.runtime_minutes),
                ("credits", movie.credits),
                ("poster", movie.poster_path),
            )
            if not present
        ]
        items.append(
            {
                "id": movie.id,
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "original_title": movie.original_title,
                "poster_path": movie.poster_path,
                "year": movie.release_date.year if movie.release_date else None,
                "language": movie.original_language,
                "imdb_id": imdb_id,
                "imdb_rating": rating.rating if rating else None,
                "theatrical_date": movie.theatrical_release_date or movie.release_date,
                "ott_platform": canonical.provider if canonical else None,
                "ott_release_date": canonical.ott_release_date if canonical and canonical.verification_status == "CONFIRMED" else None,
                "ott_status": canonical.verification_status if canonical else "UNKNOWN",
                "trailer": bool(trailer_row),
                "trailer_key": trailer_row.video_key if trailer_row else None,
                "metadata_health": "HEALTHY" if not missing else "INCOMPLETE",
                "metadata_missing": missing,
                "image_health": "HEALTHY" if movie.poster_path and movie.backdrop_path else "INCOMPLETE",
                "created_at": movie.created_at,
                "updated_at": movie.updated_at,
            }
        )
    return _pagination(total, page, page_size) | {"items": items}


def _release_item(movie: Movie, availability: OttAvailability, source_count: int) -> dict:
    today = site_date()
    if availability.verification_status == "CONFLICTING":
        status = "CONFLICTING"
    elif availability.verification_status == "NEEDS_REVIEW":
        status = "NEEDS_REVIEW"
    elif not availability.provider:
        status = "UNKNOWN"
    elif not availability.ott_release_date:
        status = "PLATFORM_ONLY"
    elif availability.ott_release_date > today:
        status = "UPCOMING"
    else:
        status = "RELEASED"
    return {
        "id": availability.id,
        "movie_id": movie.id,
        "movie": movie.title,
        "poster": movie.poster_path,
        "language": movie.original_language,
        "theatrical_date": movie.theatrical_release_date or movie.release_date,
        "platform": availability.provider,
        "ott_release_date": availability.ott_release_date,
        "status": status,
        "verification_status": availability.verification_status,
        "confidence": availability.confidence,
        "country": availability.country,
        "source_count": source_count,
        "last_verified": availability.verified_at or availability.last_checked,
        "next_check": None,
    }


@router.get("/ott-overview")
def ott_overview(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    coverage = _ott_coverage(db)
    languages = []
    for code, label in (("ml", "Malayalam"), ("ta", "Tamil"), ("te", "Telugu"), ("hi", "Hindi"), ("kn", "Kannada")):
        total = db.query(Movie).filter(Movie.original_language == code).count()
        platform_known = db.query(func.count(func.distinct(OttAvailability.movie_id))).join(Movie, Movie.id == OttAvailability.movie_id).filter(Movie.original_language == code, OttAvailability.country == "IN").scalar() or 0
        confirmed = db.query(func.count(func.distinct(OttAvailability.movie_id))).join(Movie, Movie.id == OttAvailability.movie_id).filter(Movie.original_language == code, OttAvailability.country == "IN", OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date.is_not(None)).scalar() or 0
        languages.append({"code": code, "language": label, "movies": total, "platform_known": platform_known, "confirmed_date": confirmed, "missing": max(0, total - platform_known)})
    def releases(upcoming: bool):
        query = db.query(Movie, OttAvailability).join(OttAvailability, OttAvailability.movie_id == Movie.id).filter(OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date.is_not(None))
        query = query.filter(OttAvailability.ott_release_date > site_date()) if upcoming else query.filter(OttAvailability.ott_release_date <= site_date(), OttAvailability.ott_release_date >= site_date() - timedelta(days=30))
        rows = query.options(selectinload(Movie.ott_availabilities)).order_by(OttAvailability.ott_release_date.asc() if upcoming else OttAvailability.ott_release_date.desc()).limit(12).all()
        return [
            _release_item(movie, item, db.query(OttEvidence).filter(OttEvidence.movie_id == movie.id, OttEvidence.source_url.is_not(None)).count())
            for movie, item in rows
        ]
    return {"coverage": coverage, "by_language": languages, "upcoming": releases(True), "recent": releases(False)}


@router.get("/ott-command-center")
def ott_command_center(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    """Evidence, agreement, provider, language, and accuracy-gate coverage."""
    today = site_date()
    total = db.query(Movie).count()
    platform_known = db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.country == "IN", OttAvailability.provider.is_not(None)).scalar() or 0
    platform_confirmed = db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.country == "IN", OttAvailability.verification_status.in_(["PLATFORM_CONFIRMED", "CONFIRMED"])).scalar() or 0
    date_known = db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.country == "IN", OttAvailability.ott_release_date.is_not(None)).scalar() or 0
    date_confirmed = db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.country == "IN", OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date.is_not(None)).scalar() or 0
    current_states = dict(
        db.query(OttReconciliationDecision.state, func.count(func.distinct(OttReconciliationDecision.movie_id)))
        .filter(OttReconciliationDecision.is_current.is_(True), OttReconciliationDecision.country == "IN")
        .group_by(OttReconciliationDecision.state)
        .all()
    )
    stale = db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(
        OttAvailability.country == "IN",
        or_(OttAvailability.last_seen_at.is_(None), OttAvailability.last_seen_at < datetime.now(timezone.utc) - timedelta(days=30)),
    ).scalar() or 0
    coverage = dict(
        db.query(OttEvidence.source_type, func.count(func.distinct(OttEvidence.movie_id)))
        .filter(OttEvidence.source_url.is_not(None), OttEvidence.rejected_at.is_(None))
        .group_by(OttEvidence.source_type)
        .all()
    )
    decisions = {
        (row.movie_id, row.platform): row
        for row in db.query(OttReconciliationDecision).filter(OttReconciliationDecision.is_current.is_(True), OttReconciliationDecision.country == "IN").all()
    }
    agreement = {}
    evidence_rows = db.query(OttEvidence).filter(OttEvidence.source_url.is_not(None), OttEvidence.rejected_at.is_(None), OttEvidence.country == "IN", OttEvidence.platform.is_not(None)).all()
    for evidence in evidence_rows:
        key = normalized_source_type(evidence.source_type)
        bucket = agreement.setdefault(key, {"platform_compared": 0, "platform_agreed": 0, "date_compared": 0, "date_agreed": 0})
        decision = decisions.get((evidence.movie_id, evidence.platform))
        if not decision:
            continue
        bucket["platform_compared"] += 1
        if decision.platform == evidence.platform:
            bucket["platform_agreed"] += 1
        if evidence.release_date and decision.release_date:
            bucket["date_compared"] += 1
            if evidence.release_date == decision.release_date:
                bucket["date_agreed"] += 1
    for bucket in agreement.values():
        bucket["platform_agreement"] = round(bucket["platform_agreed"] / bucket["platform_compared"] * 100, 1) if bucket["platform_compared"] else None
        bucket["date_agreement"] = round(bucket["date_agreed"] / bucket["date_compared"] * 100, 1) if bucket["date_compared"] else None
    by_language = []
    for code, label in (("ml", "Malayalam"), ("ta", "Tamil"), ("te", "Telugu"), ("hi", "Hindi"), ("kn", "Kannada")):
        movies = db.query(Movie).filter(Movie.original_language == code).count()
        known = db.query(func.count(func.distinct(OttAvailability.movie_id))).join(Movie, Movie.id == OttAvailability.movie_id).filter(Movie.original_language == code, OttAvailability.country == "IN").scalar() or 0
        confirmed = db.query(func.count(func.distinct(OttAvailability.movie_id))).join(Movie, Movie.id == OttAvailability.movie_id).filter(Movie.original_language == code, OttAvailability.country == "IN", OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date.is_not(None)).scalar() or 0
        conflicts = db.query(func.count(func.distinct(OttReconciliationDecision.movie_id))).join(Movie, Movie.id == OttReconciliationDecision.movie_id).filter(Movie.original_language == code, OttReconciliationDecision.is_current.is_(True), OttReconciliationDecision.state == "CONFLICTING").scalar() or 0
        by_language.append({"code": code, "language": label, "movies": movies, "platform_known": known, "date_confirmed": confirmed, "unknown": max(0, movies - known), "conflicts": conflicts})
    provider_names = ("tmdb_justwatch", "streaming_availability", "watchmode", "ottplay", "tavily")
    health_rows = {row.provider: row for row in db.query(OttProviderHealth).filter(OttProviderHealth.provider.in_(provider_names)).all()}
    enabled = {
        "tmdb_justwatch": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
        "streaming_availability": bool(settings.STREAMING_AVAILABILITY_ENABLED and settings.STREAMING_AVAILABILITY_API_KEY),
        "watchmode": bool(settings.WATCHMODE_ENABLED and settings.WATCHMODE_API_KEY),
        "ottplay": bool(settings.OTTPLAY_ENABLED and settings.OTTPLAY_ADAPTER_URL),
        "tavily": bool(settings.TAVILY_API_KEY),
    }
    budget = OTTApiBudgetManager(db)
    providers = []
    for name in provider_names:
        health = health_rows.get(name)
        providers.append({
            "provider": name,
            "enabled": enabled[name],
            "status": health.status if health else ("HEALTHY" if enabled[name] else "DISABLED"),
            "last_success": health.last_success_at if health else None,
            "last_failure": health.last_failure_at if health else None,
            "last_error": health.last_error if health else None,
            "latency_ms": health.last_latency_ms if health else None,
            "requests": health.request_count if health else 0,
            "success_rate": round((health.success_count / health.request_count) * 100, 1) if health and health.request_count else None,
            "match_rate": round((health.match_count / health.request_count) * 100, 1) if health and health.request_count else None,
            "budget": budget.snapshot(name),
        })
    gold_state = db.query(OperationState).filter_by(name="ott.gold_set_accuracy").first()
    web_state = db.query(OperationState).filter_by(name="ott.web_research_one_shot").first()
    gold = gold_state.details if gold_state and gold_state.details else {
        "total": db.query(OttGoldSetCase).count(), "verified": db.query(OttGoldSetCase).filter(OttGoldSetCase.manually_verified_at.is_not(None)).count(), "target": settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE * 5, "gate_passed": False, "automatic_publication_enabled": settings.OTT_INTELLIGENCE_AUTO_PUBLICATION_ENABLED,
    }
    return {
        "summary": {
            "total_movies": total,
            "platform_known": platform_known,
            "platform_confirmed": platform_confirmed,
            "ott_date_known": date_known,
            "ott_date_confirmed": date_confirmed,
            "platform_only": max(0, platform_known - date_confirmed),
            "unknown": max(0, total - platform_known),
            "upcoming": current_states.get("UPCOMING_CONFIRMED", 0),
            "released": current_states.get("RELEASED_CONFIRMED", 0),
            "conflicting": current_states.get("CONFLICTING", 0),
            "needs_review": current_states.get("NEEDS_REVIEW", 0),
            "not_found": current_states.get("NOT_FOUND", 0),
            "recently_stale": stale,
        },
        "source_coverage": {normalized_source_type(key): value for key, value in coverage.items()},
        "source_authority": SOURCE_AUTHORITY,
        "source_agreement": agreement,
        "by_language": by_language,
        "providers": providers,
        "gold_set": gold,
        "web_research": {
            "status": web_state.status if web_state else "NOT_RESEARCHED",
            "last_researched_at": web_state.last_success_at if web_state else None,
            "last_error": web_state.last_error if web_state else None,
            **(web_state.details if web_state and web_state.details else {}),
        },
        "country": "IN",
        "as_of": datetime.now(timezone.utc),
    }


@router.get("/ott-observations")
def ott_observations(
    movie_id: int | None = Query(default=None, ge=1),
    provider: str | None = None,
    available: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(OttAvailabilityObservation, Movie).join(Movie, Movie.id == OttAvailabilityObservation.movie_id)
    if movie_id:
        query = query.filter(OttAvailabilityObservation.movie_id == movie_id)
    if provider:
        query = query.filter(OttAvailabilityObservation.provider.ilike(f"%{provider.strip()}%"))
    if available is not None:
        query = query.filter(OttAvailabilityObservation.available.is_(available))
    total = query.count()
    rows = query.order_by(OttAvailabilityObservation.observed_at.desc(), OttAvailabilityObservation.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": item.id, "movie_id": movie.id, "movie": movie.title, "provider": item.provider, "country": item.country, "availability_type": item.availability_type, "available": item.available, "source_type": item.source_type, "source_url": item.source_url, "observed_at": item.observed_at, "evidence_id": item.evidence_id} for item, movie in rows]}


@router.get("/ott-decisions")
def ott_decisions(
    state: str | None = None,
    max_health: float | None = Query(default=None, ge=0, le=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(OttReconciliationDecision, Movie).join(Movie, Movie.id == OttReconciliationDecision.movie_id).filter(OttReconciliationDecision.is_current.is_(True), OttReconciliationDecision.country == "IN")
    if state:
        query = query.filter(OttReconciliationDecision.state == state.upper())
    if max_health is not None:
        query = query.filter(OttReconciliationDecision.health_score <= max_health)
    total = query.count()
    rows = query.order_by(OttReconciliationDecision.health_score.asc(), OttReconciliationDecision.decided_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": item.id, "movie_id": movie.id, "movie": movie.title, "language": movie.original_language, "state": item.state, "platform": item.platform, "release_date": item.release_date, "availability_type": item.availability_type, "platform_confidence": item.platform_confidence, "date_confidence": item.date_confidence, "movie_match_confidence": item.movie_match_confidence, "health_score": item.health_score, "reason": item.reason, "supporting_evidence_ids": item.supporting_evidence_ids, "conflicting_evidence_ids": item.conflicting_evidence_ids, "decided_at": item.decided_at} for item, movie in rows]}


@router.get("/ott-gold-set")
def ott_gold_set(
    language: str | None = None,
    verified: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(OttGoldSetCase, Movie).join(Movie, Movie.id == OttGoldSetCase.movie_id)
    if language:
        query = query.filter(OttGoldSetCase.language == language)
    if verified is not None:
        query = query.filter(OttGoldSetCase.manually_verified_at.is_not(None) if verified else OttGoldSetCase.manually_verified_at.is_(None))
    total = query.count()
    rows = query.order_by(OttGoldSetCase.language, OttGoldSetCase.category, Movie.title).offset((page - 1) * page_size).limit(page_size).all()
    state = db.query(OperationState).filter_by(name="ott.gold_set_accuracy").first()
    return _pagination(total, page, page_size) | {"items": [{"id": item.id, "movie_id": movie.id, "movie": movie.title, "year": (movie.release_date.year if movie.release_date else None), "language": item.language, "category": item.category, "expected_platform": item.expected_platform, "expected_release_date": item.expected_release_date, "expected_availability_type": item.expected_availability_type, "expected_state": item.expected_state, "source_url": item.source_url, "notes": item.notes, "manually_verified_at": item.manually_verified_at} for item, movie in rows], "accuracy": state.details if state and state.details else None}


@router.post("/ott-gold-set/generate", dependencies=[Depends(require_same_origin)])
def generate_ott_gold_set(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    result = OttGoldSetService(db).generate()
    accuracy = OttGoldSetService(db).evaluate()
    _audit(db, "ott_gold_set_generated", "ott_gold_set", None, f"{result['total']} cases")
    db.commit()
    return result | {"accuracy": accuracy}


@router.patch("/ott-gold-set/{case_id}", dependencies=[Depends(require_same_origin)])
def update_ott_gold_set(case_id: int, payload: GoldSetUpdate, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    item = db.get(OttGoldSetCase, case_id)
    if not item:
        raise HTTPException(404, "Gold-set case not found")
    if not payload.source_url and not payload.notes:
        raise HTTPException(422, "Manual ground truth requires a source URL or verification notes")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.manually_verified_at = datetime.now(timezone.utc)
    db.commit()
    accuracy = OttGoldSetService(db).evaluate()
    _audit(db, "ott_gold_set_verified", "movie", item.movie_id, payload.expected_state)
    db.commit()
    return {"id": item.id, "verified": True, "accuracy": accuracy}


@router.post("/ott-intelligence/movies/{movie_id}/refresh", dependencies=[Depends(require_same_origin)])
def refresh_ott_intelligence(movie_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    if not db.get(Movie, movie_id):
        raise HTTPException(404, "Movie not found")
    from app.workers.celery_app import celery_app

    task = celery_app.send_task("operations.ott_intelligence_movie", args=[movie_id])
    _audit(db, "ott_intelligence_refresh_queued", "movie", movie_id)
    db.commit()
    return {"queued": True, "task_id": task.id, "movie_id": movie_id}


@router.post("/ott-intelligence/{period}/run", dependencies=[Depends(require_same_origin)])
def run_ott_intelligence(period: str, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    if period not in {"daily", "weekly"}:
        raise HTTPException(422, "Period must be daily or weekly")
    from app.workers.celery_app import celery_app

    task = celery_app.send_task(f"operations.ott_intelligence_{period}")
    _audit(db, "ott_intelligence_run_queued", "job", period)
    db.commit()
    return {"queued": True, "task_id": task.id, "period": period}


@router.get("/ott-releases")
def ott_releases(
    search: str | None = None,
    status: str | None = None,
    language: str | None = None,
    platform: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(Movie, OttAvailability).join(OttAvailability, OttAvailability.movie_id == Movie.id)
    if search:
        query = query.filter(or_(Movie.title.ilike(f"%{search.strip()}%"), Movie.original_title.ilike(f"%{search.strip()}%")))
    if language:
        query = query.filter(Movie.original_language == language)
    if platform:
        query = query.filter(OttAvailability.provider.ilike(f"%{platform.strip()}%"))
    if status == "UPCOMING":
        query = query.filter(OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date > site_date())
    elif status == "RELEASED":
        query = query.filter(OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date <= site_date())
    elif status == "PLATFORM_ONLY":
        query = query.filter(OttAvailability.ott_release_date.is_(None))
    elif status in {"NEEDS_REVIEW", "CONFLICTING"}:
        query = query.filter(OttAvailability.verification_status == status)
    total = query.count()
    rows = query.order_by(OttAvailability.ott_release_date.desc().nullslast(), OttAvailability.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    movie_ids = [movie.id for movie, _ in rows]
    counts = dict(db.query(OttEvidence.movie_id, func.count(OttEvidence.id)).filter(OttEvidence.movie_id.in_(movie_ids), OttEvidence.source_url.is_not(None)).group_by(OttEvidence.movie_id).all()) if movie_ids else {}
    return _pagination(total, page, page_size) | {"items": [_release_item(movie, item, counts.get(movie.id, 0)) for movie, item in rows]}


@router.get("/sources")
def source_health(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    return {"items": _source_health(db), "email": _email_health(db)}


@router.post("/sources/{source}/run", dependencies=[Depends(require_same_origin)])
def run_source(source: str, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    source = source.lower()
    if source not in SOURCES:
        raise HTTPException(422, "Only OTTplay and JustWatch adapter jobs can be started here")
    service = OttSourceSyncService(db, source)
    snapshot = service.snapshot()
    if not snapshot["configured"]:
        raise HTTPException(409, f"{source.title()} adapter is not enabled and configured")
    if snapshot["status"] in {"QUEUED", "RUNNING"}:
        raise HTTPException(409, f"{source.title()} sync is already running")
    state = db.query(OperationState).filter_by(name=f"source.{source}").first()
    state.status = "QUEUED"
    _audit(db, "source_sync_started", "ott_source", source)
    db.commit()
    from app.workers.celery_app import celery_app
    task_name = "sources.ottplay_sync" if source == "ottplay" else "sources.justwatch_refresh"
    queued = celery_app.send_task(task_name)
    return {"queued": True, "task_id": queued.id, "source": source}


@router.get("/sources/{source}/releases")
def source_releases(
    source: str,
    status: str = "UNMATCHED",
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    source = source.lower()
    if source not in SOURCES:
        raise HTTPException(404, "Unknown OTT source")
    query = db.query(OttSourceRelease).filter(OttSourceRelease.source == source)
    if status:
        query = query.filter(OttSourceRelease.status == status)
    total = query.count()
    rows = query.order_by(OttSourceRelease.release_date.desc().nullslast(), OttSourceRelease.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for item in rows:
        token = next((part for part in _normalized_admin_title(item.title).split() if len(part) >= 3), item.title[:20])
        matches = db.query(Movie).filter(or_(Movie.title.ilike(f"%{token}%"), Movie.original_title.ilike(f"%{token}%"))).order_by(Movie.popularity.desc().nullslast()).limit(5).all()
        items.append({
            "id": item.id, "title": item.title, "original_title": item.original_title,
            "date": item.release_date, "platform": item.platform, "language": item.language,
            "source_url": item.source_url, "status": item.status, "matched_movie_id": item.matched_movie_id,
            "match_reason": item.match_reason,
            "potential_matches": [{"id": movie.id, "title": movie.title, "year": movie.release_date.year if movie.release_date else None, "language": movie.original_language} for movie in matches],
        })
    return _pagination(total, page, page_size) | {"items": items}


def _normalized_admin_title(value: str | None) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


@router.patch("/sources/releases/{release_id}", dependencies=[Depends(require_same_origin)])
def update_source_release(
    release_id: int,
    payload: SourceReleaseAction,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    item = db.get(OttSourceRelease, release_id)
    if not item:
        raise HTTPException(404, "Source release not found")
    if payload.action in {"match", "research"}:
        movie_id = payload.movie_id or item.matched_movie_id
        movie = db.get(Movie, movie_id) if movie_id else None
        if not movie:
            raise HTTPException(422, "A valid local movie is required")
        item.status = "MATCHED"
        item.matched_movie_id = movie.id
        item.match_reason = "Manual administrator match"
        if payload.action == "research":
            OttResearchService(db).queue_movie(movie.id)
        if item.source_url and item.platform:
            duplicate = db.query(OttEvidence.id).filter_by(movie_id=movie.id, source_type=item.source, source_url=item.source_url, platform=item.platform, release_date=item.release_date).first()
            if not duplicate:
                OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD).record_evidence(
                    movie.id, platform=item.platform, release_date=item.release_date,
                    source_url=item.source_url, source_title=item.title,
                    confidence=82.0 if item.source == "ottplay" else 75.0,
                    summary="Manually matched source adapter record", source_type=item.source,
                    source_name=item.source.title(), country="IN", inspected=True,
                )
    else:
        item.status = {"ignore": "IGNORED", "tv_series": "TV_SERIES", "duplicate": "DUPLICATE"}[payload.action]
        item.matched_movie_id = None
    _audit(db, "ott_source_release_updated", "ott_source_release", item.id, payload.action)
    db.commit()
    return {"id": item.id, "status": item.status, "matched_movie_id": item.matched_movie_id}


@router.get("/system-health")
def system_health(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    db.execute(select(1)).scalar_one()
    redis_status, queue_depth, redis_error = "DOWN", None, None
    try:
        import redis
        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        redis_status = "HEALTHY"
        queue_depth = client.llen("celery")
    except Exception as exc:
        redis_error = type(exc).__name__
    worker_status, worker_heartbeat = "DEGRADED", None
    try:
        from app.workers.celery_app import celery_app
        replies = celery_app.control.inspect(timeout=1).ping() or {}
        if replies:
            worker_status = "HEALTHY"
            worker_heartbeat = datetime.now(timezone.utc)
    except Exception:
        worker_status = "DOWN" if redis_status == "DOWN" else "DEGRADED"
    recent_success = db.query(func.max(OperationState.last_success_at)).scalar()
    scheduler_status = "HEALTHY" if recent_success and recent_success >= datetime.now(timezone.utc) - timedelta(days=2) else "DEGRADED"
    return {
        "services": [
            {"name": "API", "status": "HEALTHY", "last_heartbeat": datetime.now(timezone.utc)},
            {"name": "PostgreSQL", "status": "HEALTHY", "last_heartbeat": datetime.now(timezone.utc)},
            {"name": "Redis", "status": redis_status, "last_error": redis_error, "queue_depth": queue_depth},
            {"name": "Celery worker", "status": worker_status, "last_heartbeat": worker_heartbeat, "queue_depth": queue_depth},
            {"name": "Scheduler", "status": scheduler_status, "last_heartbeat": recent_success},
            {"name": "Frontend/backend connectivity", "status": "HEALTHY", "last_heartbeat": datetime.now(timezone.utc)},
        ]
    }


@router.get("/audit")
def audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_session),
):
    query = db.query(AdminAuditLog)
    total = query.count()
    rows = query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": row.id, "timestamp": row.created_at, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "summary": row.summary} for row in rows]}


@router.post("/email/retry-failed", dependencies=[Depends(require_same_origin)])
def retry_failed_email(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    rows = db.query(MovieRequest).filter(or_(MovieRequest.confirmation_email_status == "FAILED", MovieRequest.admin_notification_email_status == "FAILED", MovieRequest.completion_email_status == "FAILED", MovieRequest.rejection_email_status == "FAILED")).order_by(MovieRequest.updated_at.asc()).limit(50).all()
    attempted = sent = 0
    service = MovieRequestEmailService(db)
    for item in rows:
        for kind in EMAIL_KINDS:
            if getattr(item, f"{kind}_email_status") == "FAILED":
                result = service.send(item, kind)
                attempted += 1
                sent += int(result.get("status") == "SENT")
    _audit(db, "failed_emails_retried", "movie_request_email", None, f"Attempted {attempted}; sent {sent}")
    db.commit()
    return {"attempted": attempted, "sent": sent}


@router.post("/email/test", dependencies=[Depends(require_same_origin)])
def test_email(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    if not (settings.SMTP_HOST and settings.SMTP_FROM and settings.ADMIN_NOTIFICATION_EMAIL):
        raise HTTPException(409, "SMTP and the administrator notification email must be configured")
    from app.services.notification_service import NotificationService
    sent = NotificationService(db).notify(
        "Indian OTT Tracker administrator email test succeeded.",
        "info",
        f"admin-email-test:{datetime.now(timezone.utc).isoformat()}",
        0,
        channels=("email",),
    )
    _audit(db, "admin_email_tested", "smtp", None, "SENT" if sent else "FAILED")
    db.commit()
    if not sent:
        raise HTTPException(502, "SMTP test failed; see Notifications for the safe delivery status")
    return {"sent": True}


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
