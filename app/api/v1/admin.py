"""Cookie-authenticated operational administration API."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.rate_limit import limit
from app.core.session_auth import COOKIE, create_session, require_admin_session, require_same_origin, verify_password
from app.database.connection import get_db
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieRating, Person
from app.models.operations import BackfillRecord, DataQualityIssue, MovieRequest, NotificationLog, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.image_fallback import ImageFallbackService

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
REQUEST_STATUSES = {"PENDING", "REVIEWING", "FOUND", "ADDED", "REJECTED"}
OTT_STATUSES = {"UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "CONFIRMED", "CONFLICTING", "NOT_FOUND", "NEEDS_REVIEW", "FAILED"}
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
    query = db.query(OttEvidence, Movie).join(Movie, Movie.id == OttEvidence.movie_id)
    if status:
        if status not in OTT_STATUSES: raise HTTPException(422, "Unknown OTT status")
        query = query.filter(OttEvidence.status == status)
    total = query.count(); rows = query.order_by(OttEvidence.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": item.id, "movie_id": item.movie_id, "movie": movie.title, "status": item.status, "platform": item.platform, "date": item.release_date, "source": item.source_title, "url": item.source_url, "confidence": item.confidence, "attempts": item.attempts, "last_check": item.last_checked, "next_check": item.next_check, "error": item.notes} for item, movie in rows]}


@router.post("/ott-research/{evidence_id}/action", dependencies=[Depends(require_same_origin)])
def ott_action(evidence_id: int, payload: OttAction, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    item = db.get(OttEvidence, evidence_id)
    if not item: raise HTTPException(404, "Research item not found")
    item.status = "NEEDS_REVIEW" if payload.action == "needs_review" else "QUEUED"
    item.next_check = datetime.now(timezone.utc); item.notes = None if payload.action == "retry" else item.notes
    db.commit(); return {"id": item.id, "status": item.status, "next_check": item.next_check}


@router.get("/jobs")
def jobs(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    return [{"task": item.name, "status": item.status, "last_success": item.last_success_at, "last_failure": item.last_failure_at, "last_error": item.last_error, "cursor": item.cursor, "processed_count": item.processed_count, "total_count": item.total_count, "completed_at": item.completed_at, "remaining": max(0, item.total_count - item.processed_count) if item.total_count else None, "progress": "complete" if item.status == "COMPLETE" else "resumable"} for item in db.query(OperationState).order_by(OperationState.name)]


@router.get("/backfills")
def backfills(db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    states = {item.name: item for item in db.query(OperationState).all()}
    operations = ("tmdb.metadata_backfill", "tmdb.person_backfill", "operations.image_backfill", "ratings.imdb_backfill", "operations.ott_backfill", "operations.repair_orchestrator")
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
            "ott_queued": db.query(OttEvidence).filter(OttEvidence.status.in_(["UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "NEEDS_REVIEW", "FAILED"])).count(),
            "ott_confirmed": db.query(OttEvidence).filter(OttEvidence.status == "CONFIRMED").count(),
        },
        "configuration": {
            "tmdb": bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN),
            "imdb": bool(settings.IMDB_RATING_PROVIDER and settings.IMDB_RATING_API_URL and settings.IMDB_RATING_API_KEY),
            "google_ott_search": bool(settings.GOOGLE_SEARCH_API_KEY and settings.GOOGLE_SEARCH_ENGINE_ID),
            "generic_ott_search": bool(settings.OTT_SEARCH_API_URL and settings.OTT_SEARCH_API_KEY),
        },
    }


@router.post("/backfills/{operation}/start", dependencies=[Depends(require_same_origin)])
def start_backfill(operation: str, db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    task_name = BACKFILL_TASKS.get(operation)
    if not task_name:
        raise HTTPException(422, "Unknown backfill")
    state = db.query(OperationState).filter_by(name=task_name).first()
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


@router.get("/notifications")
def notifications(channel: str | None = None, severity: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: None = Depends(require_admin_session)):
    query = db.query(NotificationLog)
    if channel: query = query.filter(NotificationLog.channel == channel)
    if severity: query = query.filter(NotificationLog.severity == severity)
    total = query.count(); rows = query.order_by(NotificationLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _pagination(total, page, page_size) | {"items": [{"id": item.id, "timestamp": item.created_at, "channel": item.channel, "severity": item.severity, "message": item.message, "fingerprint": item.fingerprint, "status": "sent" if item.last_notified_at else "failed"} for item in rows]}
