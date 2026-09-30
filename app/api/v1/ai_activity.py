"""Administrator-only live and historical AI research activity APIs."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.session_auth import require_admin_session
from app.database.connection import get_db
from app.models.ai_research_audit import (
    AIResearchAuditRun,
    AIResearchEvent,
    AIResearchMovieActivity,
    AIResearchSource,
)


router = APIRouter(
    prefix="/api/v1/admin/ai-activity",
    tags=["Admin AI Activity"],
    dependencies=[Depends(require_admin_session)],
)


def _run(row: AIResearchAuditRun) -> dict:
    return {
        "run_id": row.run_id,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "status": row.status,
        "trigger": row.trigger,
        "initiated_by": row.initiated_by,
        "mode": row.mode,
        "provider": row.provider,
        "model": row.model,
        "movies_total": row.movies_total,
        "movies_processed": row.movies_processed,
        "successful": row.successful,
        "needs_review": row.needs_review,
        "conflicts": row.conflicts,
        "no_reliable_result": row.no_reliable_result,
        "provider_failures": row.provider_failures,
        "sources_discovered": row.sources_discovered,
        "sources_inspected": row.sources_inspected,
        "database_updates": row.database_updates,
        "fallback_count": row.fallback_count,
        "duration_seconds": row.duration_seconds,
        "error_summary": row.error_summary,
    }


def _movie(row: AIResearchMovieActivity) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "movie_id": row.movie_id,
        "tmdb_id": row.tmdb_id,
        "imdb_id": row.imdb_id,
        "title": row.title,
        "original_title": row.original_title,
        "release_year": row.release_year,
        "language": row.language,
        "production_countries": row.production_countries,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "status": row.status,
        "provider": row.provider,
        "model": row.model,
        "queries": row.queries,
        "sources_discovered": row.sources_discovered,
        "sources_inspected": row.sources_inspected,
        "platforms_found": row.platforms_found,
        "dates_found": row.dates_found,
        "regions_found": row.regions_found,
        "watch_types_found": row.watch_types_found,
        "identity_confidence": row.identity_confidence,
        "platform_confidence": row.platform_confidence,
        "date_confidence": row.date_confidence,
        "decision": row.decision,
        "error_type": row.error_type,
        "error_summary": row.error_summary,
        "dry_run": row.dry_run,
        "database_changed": row.database_changed,
        "before_value": row.before_value,
        "proposed_value": row.proposed_value,
        "after_value": row.after_value,
        "duration_seconds": row.duration_seconds,
    }


def _event(row: AIResearchEvent) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "movie_activity_id": row.movie_activity_id,
        "movie_id": row.movie_id,
        "sequence": row.sequence,
        "occurred_at": row.occurred_at,
        "event_type": row.event_type,
        "message": row.message,
        "metadata": row.metadata_json,
    }


def _source(row: AIResearchSource) -> dict:
    return {
        "id": row.id,
        "url": row.url,
        "domain": row.domain,
        "page_title": row.page_title,
        "source_tier": row.source_tier,
        "discovered_at": row.discovered_at,
        "fetched_at": row.fetched_at,
        "fetch_status": row.fetch_status,
        "identity_match": row.identity_match,
        "platform_evidence": row.platform_evidence,
        "date_evidence": row.date_evidence,
        "region_evidence": row.region_evidence,
        "watch_type_evidence": row.watch_type_evidence,
        "accepted": row.accepted,
        "rejection_reason": row.rejection_reason,
        "evidence_excerpt": row.evidence_excerpt,
    }


def _page(items: list, total: int, page: int, page_size: int) -> dict:
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, math.ceil(total / page_size)),
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    today = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    active = (
        db.query(AIResearchAuditRun)
        .filter(AIResearchAuditRun.status == "RUNNING")
        .order_by(AIResearchAuditRun.started_at.desc())
        .all()
    )
    today_movies = db.query(AIResearchMovieActivity).filter(
        AIResearchMovieActivity.started_at >= today
    ).all()
    current_movies = []
    for run in active:
        item = (
            db.query(AIResearchMovieActivity)
            .filter_by(run_id=run.run_id, status="RUNNING")
            .order_by(AIResearchMovieActivity.started_at.desc())
            .first()
        )
        if item:
            current_movies.append(_movie(item))
    error_counts = dict(
        db.query(AIResearchMovieActivity.error_type, func.count(AIResearchMovieActivity.id))
        .filter(
            AIResearchMovieActivity.started_at >= today,
            AIResearchMovieActivity.error_type.is_not(None),
        )
        .group_by(AIResearchMovieActivity.error_type)
        .all()
    )
    return {
        "current": {
            "active_runs": [_run(item) for item in active],
            "current_movies": current_movies,
        },
        "today": {
            "movies_researched": len(today_movies),
            "successful": sum(item.decision == "AUTO_ACCEPT" for item in today_movies),
            "needs_review": sum(item.decision == "NEEDS_REVIEW" for item in today_movies),
            "conflicts": sum(item.decision == "CONFLICT" for item in today_movies),
            "no_reliable_result": sum(item.decision == "NO_RELIABLE_RESULT" for item in today_movies),
            "provider_failures": sum(bool(item.error_type) for item in today_movies),
            "database_updates": sum(item.database_changed for item in today_movies),
            "sources_inspected": sum(item.sources_inspected for item in today_movies),
        },
        "provider": {
            "name": settings.AI_RESEARCH_PROVIDER or "not configured",
            "status": "CONFIGURED" if settings.AI_RESEARCH_API_URL and settings.AI_RESEARCH_API_KEY else "NOT_CONFIGURED",
            "model": settings.AI_RESEARCH_MODEL or None,
            "fallback_count": sum(item.fallback_count for item in active),
            "errors": error_counts,
        },
        "discord_configured": bool(
            settings.AI_RESEARCH_DISCORD_ENABLED
            and settings.AI_RESEARCH_DISCORD_WEBHOOK_URL
        ),
    }


@router.get("/runs")
def runs(
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    trigger: str | None = None,
    mode: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(AIResearchAuditRun)
    if date_from:
        query = query.filter(AIResearchAuditRun.started_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        query = query.filter(AIResearchAuditRun.started_at < datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    if status:
        query = query.filter(AIResearchAuditRun.status == status.upper())
    if trigger:
        query = query.filter(AIResearchAuditRun.trigger == trigger.upper())
    if mode:
        query = query.filter(AIResearchAuditRun.mode == mode.upper())
    if provider:
        query = query.filter(AIResearchAuditRun.provider.ilike(f"%{provider[:100]}%"))
    if model:
        query = query.filter(AIResearchAuditRun.model.ilike(f"%{model[:200]}%"))
    total = query.count()
    rows = query.order_by(AIResearchAuditRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page([_run(item) for item in rows], total, page, page_size)


@router.get("/runs/{run_id}")
def run_detail(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    run = db.get(AIResearchAuditRun, run_id)
    if not run:
        raise HTTPException(404, "AI research run not found")
    query = db.query(AIResearchMovieActivity).filter_by(run_id=run_id)
    total = query.count()
    movies = query.order_by(AIResearchMovieActivity.started_at).offset((page - 1) * page_size).limit(page_size).all()
    run_events = (
        db.query(AIResearchEvent)
        .filter(AIResearchEvent.run_id == run_id, AIResearchEvent.movie_activity_id.is_(None))
        .order_by(AIResearchEvent.id)
        .all()
    )
    return {
        "run": _run(run),
        "movies": _page([_movie(item) for item in movies], total, page, page_size),
        "events": [_event(item) for item in run_events],
    }


@router.get("/movies")
def movies(
    date_from: date | None = None,
    date_to: date | None = None,
    run_id: str | None = None,
    title: str | None = None,
    movie_id: int | None = None,
    tmdb_id: int | None = None,
    imdb_id: str | None = None,
    status: str | None = None,
    decision: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    dry_run: bool | None = None,
    database_changed: bool | None = None,
    needs_review: bool | None = None,
    conflict: bool | None = None,
    provider_failure: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(AIResearchMovieActivity)
    if date_from:
        query = query.filter(AIResearchMovieActivity.started_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        query = query.filter(AIResearchMovieActivity.started_at < datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    if run_id:
        query = query.filter(AIResearchMovieActivity.run_id == run_id[:64])
    if title:
        query = query.filter(AIResearchMovieActivity.title.ilike(f"%{title[:200]}%"))
    if movie_id is not None:
        query = query.filter(AIResearchMovieActivity.movie_id == movie_id)
    if tmdb_id is not None:
        query = query.filter(AIResearchMovieActivity.tmdb_id == tmdb_id)
    if imdb_id:
        query = query.filter(AIResearchMovieActivity.imdb_id == imdb_id[:32])
    if status:
        query = query.filter(AIResearchMovieActivity.status == status.upper())
    if decision:
        query = query.filter(AIResearchMovieActivity.decision == decision.upper())
    if provider:
        query = query.filter(AIResearchMovieActivity.provider.ilike(f"%{provider[:100]}%"))
    if model:
        query = query.filter(AIResearchMovieActivity.model.ilike(f"%{model[:200]}%"))
    if dry_run is not None:
        query = query.filter(AIResearchMovieActivity.dry_run.is_(dry_run))
    if database_changed is not None:
        query = query.filter(AIResearchMovieActivity.database_changed.is_(database_changed))
    if needs_review is not None:
        query = query.filter(AIResearchMovieActivity.decision == "NEEDS_REVIEW" if needs_review else AIResearchMovieActivity.decision != "NEEDS_REVIEW")
    if conflict is not None:
        query = query.filter(AIResearchMovieActivity.decision == "CONFLICT" if conflict else AIResearchMovieActivity.decision != "CONFLICT")
    if provider_failure is not None:
        query = query.filter(AIResearchMovieActivity.error_type.is_not(None) if provider_failure else AIResearchMovieActivity.error_type.is_(None))
    total = query.count()
    rows = query.order_by(AIResearchMovieActivity.started_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page([_movie(item) for item in rows], total, page, page_size)


@router.get("/movies/{activity_id}")
def movie_detail(activity_id: int, db: Session = Depends(get_db)):
    movie = db.get(AIResearchMovieActivity, activity_id)
    if not movie:
        raise HTTPException(404, "AI research movie activity not found")
    events = db.query(AIResearchEvent).filter_by(movie_activity_id=movie.id).order_by(AIResearchEvent.sequence, AIResearchEvent.id).all()
    sources = db.query(AIResearchSource).filter_by(movie_activity_id=movie.id).order_by(AIResearchSource.id).all()
    return {
        "movie": _movie(movie),
        "events": [_event(item) for item in events],
        "sources": [_source(item) for item in sources],
    }


@router.get("/live")
def live(
    after_event_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=250),
    db: Session = Depends(get_db),
):
    events = (
        db.query(AIResearchEvent)
        .filter(AIResearchEvent.id > after_event_id)
        .order_by(AIResearchEvent.id)
        .limit(limit)
        .all()
    )
    active = db.query(AIResearchAuditRun).filter_by(status="RUNNING").order_by(AIResearchAuditRun.started_at.desc()).all()
    return {
        "active_runs": [_run(item) for item in active],
        "events": [_event(item) for item in events],
        "last_event_id": events[-1].id if events else after_event_id,
    }
