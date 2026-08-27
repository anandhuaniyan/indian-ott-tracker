"""Cookie-authenticated operational administration API."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.core.rate_limit import limit
from app.core.session_auth import COOKIE, create_session, require_admin_session, require_same_origin, verify_password
from app.database.connection import get_db
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieRating, Person
from app.models.operations import BackfillRecord, DataQualityIssue, MovieRequest, NotificationLog, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.image_fallback import ImageFallbackService
from app.services.operations import OttResearchService, ResearchUsageService
from app.services.release_status import (
    ELIGIBILITY_LABELS,
    RELEASE_LABELS,
    ReleaseStatusService,
    best_canonical_ott,
)
from app.services.tmdb.movie_service import TMDbMovieService

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
REQUEST_STATUSES = {"PENDING", "REVIEWING", "FOUND", "ADDED", "REJECTED"}
OTT_STATUSES = {"UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "CONFIRMED", "CONFLICTING", "NOT_FOUND", "NEEDS_REVIEW", "FAILED", "WAITING_RELEASE", "METADATA_REPAIR", "TOO_OLD", "ELIGIBLE"}
BACKFILL_TASKS = {
    "metadata": "tmdb.metadata_backfill", "people": "tmdb.person_backfill",
    "images": "operations.image_backfill", "imdb": "ratings.imdb_backfill",
    "ott": "operations.ott_backfill", "all": "operations.repair_orchestrator",
}


class Login(BaseModel):
    password: str = Field(min_length=8, max_length=512)


class RequestStatus(BaseModel):
    status: str


class OttAction(BaseModel):
    action: str = Field(pattern="^(requeue|retry|needs_review)$")


def _pagination(total: int, page: int, page_size: int):
    return {"total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


@router.post("/login")
def login(payload: Login, response: Response, request: Request):
    limit(request, "admin-login", 5, 300)
    if not verify_password(payload.password):
        raise HTTPException(401, "Invalid credentials")
    response.set_cookie(COOKIE, create_session(), httponly=True, secure=settings.ENVIRONMENT == "production", samesite="strict", max_age=28800, path="/")
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
    recent_notifications = db.query(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(10).all()
    jobs = db.query(OperationState).order_by(OperationState.name).all()
    return {
        "total_movies": db.query(Movie).count(),
        "movies_with_issues": db.query(func.count(func.distinct(DataQualityIssue.movie_id))).filter(open_filter).scalar() or 0,
        "open_issues": db.query(DataQualityIssue).filter(open_filter).count(),
        "image_issues": db.query(DataQualityIssue).filter(open_filter, DataQualityIssue.issue_type.in_(["missing_poster", "broken_poster", "missing_backdrop", "broken_backdrop", "missing_logo", "missing_profile", "broken_profile", "image_unresolved"])).count(),
        "missing_ott": db.query(DataQualityIssue).filter(open_filter, DataQualityIssue.issue_type.in_(["missing_ott", "missing_ott_provider", "missing_ott_release_date"])).count(),
        "conflicting_ott": db.query(OttEvidence).filter(OttEvidence.status == "CONFLICTING").count(),
        "ott_queue": db.query(OttEvidence).filter(OttEvidence.status.in_(["UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "NEEDS_REVIEW", "FAILED"])).count(),
        "failed_research": db.query(OttEvidence).filter(OttEvidence.status == "FAILED").count(),
        "pending_requests": db.query(MovieRequest).filter(MovieRequest.status == "PENDING").count(),
        "recent_notifications": [{"id": x.id, "timestamp": x.created_at, "channel": x.channel, "severity": x.severity, "message": x.message} for x in recent_notifications],
        "jobs": [{"task": x.name, "cursor": x.cursor, "processed_count": x.processed_count, "last_success": x.last_success_at, "last_failure": x.last_failure_at, "last_error": x.last_error} for x in jobs],
    }


@router.get("/requests")
def requests(search: str | None = None, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    query = db.query(MovieRequest)
    if status:
        if status not in REQUEST_STATUSES: raise HTTPException(422, "Unknown request status")
        query = query.filter(MovieRequest.status == status)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(MovieRequest.movie_name.ilike(term), MovieRequest.email.ilike(term), MovieRequest.request_id.ilike(term)))
    total = query.count()
    rows = query.order_by(MovieRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [_request(item) for item in rows]}


def _request(item: MovieRequest):
    return {"request_id": item.request_id, "movie_name": item.movie_name, "email": item.email, "release_year": item.release_year, "language": item.language, "details": item.details, "status": item.status, "created_at": item.created_at, "updated_at": item.updated_at}


@router.get("/requests/{request_id}")
def request_detail(request_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    item = db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item: raise HTTPException(404, "Request not found")
    return _request(item)


@router.patch("/requests/{request_id}", dependencies=[Depends(require_same_origin)])
def update_request(request_id: str, payload: RequestStatus, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    if payload.status not in REQUEST_STATUSES: raise HTTPException(422, "Unknown request status")
    item = db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item: raise HTTPException(404, "Request not found")
    item.status = payload.status; db.commit(); return _request(item)


@router.get("/data-health")
def data_health(issue_type: str | None = None, severity: str | None = None, status: str = "open", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    query = db.query(DataQualityIssue, Movie).outerjoin(Movie, Movie.id == DataQualityIssue.movie_id)
    if status == "open": query = query.filter(DataQualityIssue.resolved_at.is_(None))
    elif status == "resolved": query = query.filter(DataQualityIssue.resolved_at.is_not(None))
    if issue_type: query = query.filter(DataQualityIssue.issue_type == issue_type)
    if severity: query = query.filter(DataQualityIssue.severity == severity)
    total = query.count()
    rows = query.order_by(DataQualityIssue.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": issue.id, "movie_id": issue.movie_id, "movie": movie.title if movie else None, "issue_type": issue.issue_type, "severity": issue.severity, "description": issue.detail, "created_at": issue.created_at, "status": "resolved" if issue.resolved_at else "open"} for issue, movie in rows]}


@router.get("/images")
def images(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    image_types = ["missing_poster", "broken_poster", "missing_backdrop", "broken_backdrop", "missing_logo", "missing_profile", "broken_profile", "image_recovered", "image_unresolved"]
    query = db.query(DataQualityIssue, Movie, Person).outerjoin(Movie, Movie.id == DataQualityIssue.movie_id).outerjoin(Person, Person.id == DataQualityIssue.person_id).filter(DataQualityIssue.issue_type.in_(image_types))
    if status == "recovered": query = query.filter(DataQualityIssue.resolved_at.is_not(None))
    elif status == "unresolved": query = query.filter(DataQualityIssue.resolved_at.is_(None))
    total = query.count(); rows = query.order_by(DataQualityIssue.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    counts = dict(db.query(DataQualityIssue.issue_type, func.count(DataQualityIssue.id)).filter(DataQualityIssue.issue_type.in_(image_types), DataQualityIssue.resolved_at.is_(None)).group_by(DataQualityIssue.issue_type).all())
    return _pagination(total, page, page_size) | {"counts": counts, "items": [{"id": issue.id, "movie_id": issue.movie_id, "person_id": issue.person_id, "subject": movie.title if movie else person.name if person else None, "type": issue.issue_type, "description": issue.detail, "status": "recovered" if issue.resolved_at else "unresolved", "updated_at": issue.updated_at} for issue, movie, person in rows]}


@router.post("/images/{movie_id}/retry", dependencies=[Depends(require_same_origin)])
def retry_image(movie_id: int, image_type: str = "poster", db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    movie = db.get(Movie, movie_id)
    if not movie: raise HTTPException(404, "Movie not found")
    return ImageFallbackService(db).recover_movie(movie, image_type)


@router.post("/images/people/{person_id}/retry", dependencies=[Depends(require_same_origin)])
def retry_profile(person_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    person = db.get(Person, person_id)
    if not person: raise HTTPException(404, "Person not found")
    return ImageFallbackService(db).recover_person(person)


@router.get("/ott-research")
def ott_research(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    latest_id = (
        select(func.max(OttEvidence.id))
        .where(OttEvidence.movie_id == Movie.id)
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
        if status not in OTT_STATUSES: raise HTTPException(422, "Unknown OTT status")
        if status == "ELIGIBLE":
            query = query.filter(Movie.ott_research_eligibility == "ELIGIBLE")
        elif status == "WAITING_RELEASE":
            query = query.filter(Movie.ott_research_eligibility.in_(["WAITING_RELEASE", "MIN_DELAY"]))
        else:
            query = query.filter(OttEvidence.status == status)
    total = query.count()
    rows = query.order_by(Movie.theatrical_release_date.desc().nullslast(), Movie.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for movie, evidence in rows:
        canonical = best_canonical_ott(movie)
        items.append({
            "id": evidence.id if evidence else None,
            "movie_id": movie.id,
            "movie": movie.title,
            "theatrical_release_date": movie.theatrical_release_date,
            "release_status": RELEASE_LABELS.get(movie.release_status_code, "Unknown"),
            "eligibility": movie.ott_research_eligibility,
            "eligibility_label": ELIGIBILITY_LABELS.get(movie.ott_research_eligibility, "Unclassified"),
            "status": evidence.status if evidence else "NOT_QUEUED",
            "platform": canonical.provider if canonical else evidence.platform if evidence else None,
            "date": canonical.ott_release_date if canonical else evidence.release_date if evidence else None,
            "source": evidence.source_title if evidence else canonical.source_type if canonical else None,
            "url": evidence.source_url if evidence else canonical.source_url if canonical else None,
            "confidence": evidence.confidence if evidence else canonical.confidence if canonical else 0,
            "attempts": evidence.attempts if evidence else 0,
            "last_check": evidence.last_checked if evidence else None,
            "next_check": evidence.next_check if evidence else None,
            "error": evidence.notes if evidence else None,
        })
    usage = ResearchUsageService(db)
    return _pagination(total, page, page_size) | {
        "items": items,
        "daily_usage": usage.daily_snapshot(),
        "tavily_usage": usage.monthly_snapshot(),
    }


@router.post("/ott-research/{evidence_id}/action", dependencies=[Depends(require_same_origin)])
def ott_action(evidence_id: int, payload: OttAction, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    item = db.get(OttEvidence, evidence_id)
    if not item: raise HTTPException(404, "Research item not found")
    if payload.action != "needs_review":
        movie = db.get(Movie, item.movie_id)
        _, eligibility, _ = ReleaseStatusService(db).classify_movie(movie)
        if eligibility.code != "ELIGIBLE":
            db.commit()
            raise HTTPException(409, eligibility.label)
    item.status = "NEEDS_REVIEW" if payload.action == "needs_review" else "QUEUED"
    item.next_check = datetime.now(timezone.utc); item.notes = None if payload.action == "retry" else item.notes
    db.commit(); return {"id": item.id, "status": item.status, "next_check": item.next_check}


@router.get("/jobs")
def jobs(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    return [{"task": item.name, "status": item.status, "last_success": item.last_success_at, "last_failure": item.last_failure_at, "last_error": item.last_error, "cursor": item.cursor, "processed_count": item.processed_count, "total_count": item.total_count, "completed_at": item.completed_at, "remaining": max(0, item.total_count - item.processed_count) if item.total_count else None, "progress": "complete" if item.status == "COMPLETE" else "resumable"} for item in db.query(OperationState).order_by(OperationState.name)]


@router.get("/backfills")
def backfills(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    states = {item.name: item for item in db.query(OperationState).all()}
    operations = ("tmdb.metadata_backfill", "tmdb.person_backfill", "operations.image_backfill", "ratings.imdb_backfill", "release_status_classification", "operations.ott_eligibility_backfill", "operations.ott_backfill", "operations.repair_orchestrator")
    progress = []
    for name in operations:
        state = states.get(name)
        failures = db.query(BackfillRecord).filter_by(operation=name, status="FAILED").count()
        progress.append({
            "operation": name, "status": state.status if state else "IDLE", "cursor": state.cursor if state else 0,
            "processed": state.processed_count if state else 0, "total": state.total_count if state else 0,
            "remaining": max(0, state.total_count - state.processed_count) if state and state.total_count else None,
            "failed": failures, "last_success": state.last_success_at if state else None,
            "last_failure": state.last_failure_at if state else None, "last_error": state.last_error if state else None,
            "completed_at": state.completed_at if state else None,
        })
    total_movies = db.query(Movie).count(); total_people = db.query(Person).count()
    return {
        "progress": progress,
        "coverage": {
            "movies": total_movies,
            "movies_with_cast": db.query(func.count(func.distinct(MovieCredit.movie_id))).filter(MovieCredit.credit_type == "cast").scalar() or 0,
            "movies_with_crew": db.query(func.count(func.distinct(MovieCredit.movie_id))).filter(MovieCredit.credit_type == "crew").scalar() or 0,
            "movies_with_posters": db.query(Movie).filter(Movie.poster_path.is_not(None), Movie.poster_path != "").count(),
            "movies_with_backdrops": db.query(Movie).filter(Movie.backdrop_path.is_not(None), Movie.backdrop_path != "").count(),
            "people": total_people,
            "people_with_profiles": db.query(Person).filter(Person.profile_path.is_not(None), Person.profile_path != "").count(),
            "movies_with_imdb_id": db.query(func.count(func.distinct(ExternalId.movie_id))).filter(func.lower(ExternalId.provider) == "imdb").scalar() or 0,
            "movies_with_imdb_rating": db.query(func.count(func.distinct(MovieRating.movie_id))).filter(func.lower(MovieRating.source) == "imdb").scalar() or 0,
            "movies_with_ott": db.query(func.count(func.distinct(OttAvailability.movie_id))).scalar() or 0,
            "movies_with_ott_date": db.query(func.count(func.distinct(OttAvailability.movie_id))).filter(OttAvailability.ott_release_date.is_not(None)).scalar() or 0,
            "released_movies": db.query(Movie).filter(Movie.release_status_code == "THEATRICALLY_RELEASED").count(),
            "upcoming_movies": db.query(Movie).filter(Movie.release_status_code == "UPCOMING").count(),
            "direct_to_ott_movies": db.query(Movie).filter(Movie.release_status_code == "DIRECT_TO_OTT").count(),
            "ott_research_eligible": db.query(Movie).filter(Movie.ott_research_eligibility == "ELIGIBLE").count(),
            "ott_waiting_for_release": db.query(Movie).filter(Movie.ott_research_eligibility.in_(["WAITING_RELEASE", "MIN_DELAY"])).count(),
            "ott_queued": db.query(OttEvidence).filter(OttEvidence.status.in_(["UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "NEEDS_REVIEW", "FAILED"])).count(),
            "ott_confirmed": db.query(OttEvidence).filter(OttEvidence.status == "CONFIRMED").count(),
        },
        "configuration": {
            "tmdb": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
            "imdb": bool(settings.IMDB_RATING_PROVIDER and settings.IMDB_RATING_API_URL and settings.IMDB_RATING_API_KEY),
            "google_ott_search": bool(settings.GOOGLE_SEARCH_API_KEY and settings.GOOGLE_SEARCH_ENGINE_ID),
            "generic_ott_search": bool(settings.OTT_SEARCH_API_URL and settings.OTT_SEARCH_API_KEY),
            "tavily": bool(settings.TAVILY_API_KEY or (settings.OTT_RESEARCH_PROVIDER.lower() == "tavily" and settings.OTT_SEARCH_API_KEY)),
        },
    }


@router.post("/backfills/{operation}/start", dependencies=[Depends(require_same_origin)])
def start_backfill(operation: str, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    task_name = BACKFILL_TASKS.get(operation)
    if not task_name:
        raise HTTPException(422, "Unknown backfill")
    state_name = "operations.ott_eligibility_backfill" if operation == "ott" else task_name
    state = db.query(OperationState).filter_by(name=state_name).first()
    if state and state.status == "COMPLETE":
        return {"queued": False, "task": task_name, "status": "COMPLETE", "detail": "Backfill already completed; it was not restarted"}
    if state and state.status == "RUNNING" and state.last_success_at and state.last_success_at >= datetime.now(timezone.utc) - timedelta(minutes=15):
        return {"queued": False, "task": task_name, "status": "RUNNING", "detail": "Backfill is already running"}
    from app.workers.celery_app import celery_app
    queued = celery_app.send_task(task_name)
    return {"queued": True, "task": task_name, "task_id": queued.id, "status": "QUEUED"}


@router.post("/movies/{movie_id}/repair", dependencies=[Depends(require_same_origin)])
def repair_movie(movie_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
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
        "workflow": ["metadata", "people", "images", "imdb", "release-status", "eligible-ott"],
    }


@router.post("/deep-search/movies/{tmdb_id}/import", dependencies=[Depends(require_same_origin)])
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
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 404:
            raise HTTPException(404, "TMDB movie not found") from exc
        raise HTTPException(502, "TMDB is temporarily unavailable") from exc
    if payload.get("id") != tmdb_id or not (payload.get("title") or payload.get("original_title")):
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
            return {
                "created": False,
                "queued": False,
                "status": "already_exists",
                "local_movie_id": existing.id,
                "display_id": existing.tmdb_id,
            }
        raise
    return {"created": True, "status": "imported"} | _queue_deep_repair(movie)


@router.post("/deep-search/movies/{tmdb_id}/repair", dependencies=[Depends(require_same_origin)])
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
def notifications(channel: str | None = None, severity: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    query = db.query(NotificationLog)
    if channel: query = query.filter(NotificationLog.channel == channel)
    if severity: query = query.filter(NotificationLog.severity == severity)
    total = query.count(); rows = query.order_by(NotificationLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": item.id, "timestamp": item.created_at, "channel": item.channel, "severity": item.severity, "message": item.message, "fingerprint": item.fingerprint, "status": "sent" if item.last_notified_at else "failed"} for item in rows]}
