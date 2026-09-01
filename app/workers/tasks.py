import logging
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import case, exists, func, select
from app.workers.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.operations import (
    DataHealthService,
    OttResearchService,
    ResearchUsageService,
)
from app.services.release_status import ReleaseStatusService
from app.services.image_fallback import ImageFallbackService
from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.services.ott_providers import (
    configured_ott_provider,
    inspect_source,
    source_rank,
)
from app.models.movie import Movie
from app.models.operations import (
    DataQualityIssue,
    MovieRequest,
    NotificationLog,
    OperationState,
    OttEvidence,
)
from app.models.ott_availability import OttAvailability

log = logging.getLogger(__name__)


def _run(service):
    db = SessionLocal()
    try:
        return service(db)
    finally:
        db.close()


def _continue(task, result, *, batch_size=None, countdown=2):
    """Continue one checkpointed chain without creating unbounded parallel work."""
    if (
        result.get("complete") is False
        and not result.get("stopped")
        and result.get("configured", True)
    ):
        db = SessionLocal()
        state = None
        try:
            state = (
                db.query(OperationState).filter_by(name=result.get("operation")).first()
            )
            if state:
                state.status = "RUNNING"
                db.commit()
            task.apply_async(
                kwargs={"batch_size": batch_size, "continuous": True},
                countdown=countdown,
            )
        except Exception as exc:
            if state:
                state.status = "FAILED"
                state.last_failure_at = datetime.now(timezone.utc)
                state.last_error = sanitize_error(exc)
                db.commit()
            raise
        finally:
            db.close()
    return result


@celery_app.task(
    name="operations.data_health",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def data_health():
    return _run(lambda db: DataHealthService(db).scan())


@celery_app.task(
    name="operations.ott_queue",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_queue():
    return _run(lambda db: OttResearchService(db).queue_missing())


@celery_app.task(
    bind=True,
    name="operations.release_status",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def release_status(self, batch_size=1000, restart_completed=False):
    result = _run(
        lambda db: ReleaseStatusService(db).classify_batch(
            batch_size, restart_completed=restart_completed
        )
    )
    if not result.get("complete"):
        self.apply_async(
            kwargs={"batch_size": batch_size, "restart_completed": False},
            countdown=2,
        )
    return result


@celery_app.task(
    name="operations.image_recovery",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def image_recovery():
    return _run(lambda db: ImageFallbackService(db).recover_batch())


@celery_app.task(
    name="operations.image_health",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def image_health():
    return _run(lambda db: ImageFallbackService(db).scan())


@celery_app.task(
    name="movies.discovery",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_discovery():
    from app.services.movie_discovery import MovieDiscoveryService

    return _run(lambda db: MovieDiscoveryService(db).run_regular())


@celery_app.task(
    name="movies.discovery_weekly",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_discovery_weekly():
    from app.services.movie_discovery import MovieDiscoveryService

    return _run(lambda db: MovieDiscoveryService(db).run_weekly())


@celery_app.task(
    name="tmdb.metadata_enrichment",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def metadata_enrichment():
    def run(db):
        from app.services.movie_metadata_service import MovieMetadataService

        state = db.query(OperationState).filter_by(name="metadata_enrichment").first()
        if not state:
            state = OperationState(name="metadata_enrichment")
            db.add(state)
            db.flush()
        movies = (
            db.query(Movie)
            .filter(Movie.id > state.cursor)
            .order_by(Movie.id)
            .limit(20)
            .all()
        )
        if not movies:
            state.cursor = 0
            db.commit()
            return {"processed": 0, "cycle_complete": True}
        service = MovieMetadataService(db)
        ott_service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
        for movie in movies:
            service.enrich_movie(movie)
            ReleaseStatusService(db).classify_movie(movie)
            ott_service.queue_movie(movie.id)
            state.cursor = movie.id
            state.processed_count += 1
        state.last_success_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "processed": len(movies),
            "cursor": state.cursor,
            "cycle_complete": False,
        }

    return _run(run)


@celery_app.task(
    name="ratings.imdb_refresh",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def imdb_rating_refresh():
    from app.services.rating_provider import IMDbRatingRefreshService

    return _run(lambda db: IMDbRatingRefreshService(db).refresh())


@celery_app.task(bind=True, name="ratings.imdb_id_backfill")
def imdb_id_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import IMDbIdRecoveryService

    result = _run(lambda db: IMDbIdRecoveryService(db).run(batch_size))
    return (
        _continue(self, result, batch_size=batch_size, countdown=5)
        if continuous
        else result
    )


@celery_app.task(bind=True, name="tmdb.metadata_backfill")
def metadata_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import MetadataBackfillService

    result = _run(lambda db: MetadataBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="tmdb.person_backfill")
def person_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import PersonBackfillService

    result = _run(lambda db: PersonBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="operations.image_backfill")
def image_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import ImageBackfillService

    result = _run(lambda db: ImageBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="tmdb.trailer_backfill")
def trailer_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import TrailerBackfillService

    result = _run(lambda db: TrailerBackfillService(db).run(batch_size))
    return (
        _continue(self, result, batch_size=batch_size, countdown=5)
        if continuous
        else result
    )


@celery_app.task(bind=True, name="ratings.imdb_backfill")
def imdb_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import IMDbBackfillService

    result = _run(lambda db: IMDbBackfillService(db).run(batch_size))
    return (
        _continue(self, result, batch_size=batch_size, countdown=5)
        if continuous
        else result
    )


@celery_app.task(bind=True, name="operations.ott_backfill")
def ott_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import OttQueueBackfillService

    result = _run(lambda db: OttQueueBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(name="repair.movie")
def repair_movie(movie_id: int):
    from app.services.backfill import SingleMovieRepairService

    def run(db):
        state = (
            db.query(OperationState)
            .filter_by(name=f"on_demand_repair:{movie_id}")
            .first()
        )
        if not state:
            state = OperationState(name=f"on_demand_repair:{movie_id}", total_count=1)
            db.add(state)
        state.status = "RUNNING"
        db.commit()
        try:
            result = SingleMovieRepairService(db).repair(movie_id)
            state = (
                db.query(OperationState)
                .filter_by(name=f"on_demand_repair:{movie_id}")
                .first()
            )
            state.status = "COMPLETE"
            state.processed_count = 1
            state.completed_at = datetime.now(timezone.utc)
            state.last_success_at = datetime.now(timezone.utc)
            state.last_error = None
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            state = (
                db.query(OperationState)
                .filter_by(name=f"on_demand_repair:{movie_id}")
                .first()
            )
            state.status = "FAILED"
            state.last_failure_at = datetime.now(timezone.utc)
            state.last_error = sanitize_error(exc)
            db.commit()
            raise

    return _run(run)


@celery_app.task(
    name="operations.ott_verification",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_verification():
    def run(db):
        service = OttResearchService(db)
        transitions = service.transition_release_states()
        due = (
            db.query(OttAvailability)
            .filter(
                OttAvailability.manually_verified.is_(False),
                (OttAvailability.last_checked.is_(None))
                | (
                    OttAvailability.last_checked
                    < datetime.now(timezone.utc) - timedelta(days=30)
                ),
            )
            .order_by(OttAvailability.id)
            .limit(100)
            .all()
        )
        queued = 0
        for availability in due:
            queued += int(service.queue_movie(availability.movie_id))
        db.commit()
        return {"queued": queued, "transitions": transitions}

    return _run(run)


@celery_app.task(
    name="sources.ottplay_sync",
    autoretry_for=(),
)
def ottplay_source_sync():
    from app.services.ott_source_sync import OttSourceSyncService

    return _run(lambda db: OttSourceSyncService(db, "ottplay").sync())


@celery_app.task(
    name="sources.justwatch_refresh",
    autoretry_for=(),
)
def justwatch_source_refresh():
    """An adapter failure is recorded but never fails the canonical OTT pipeline."""
    from app.services.ott_source_sync import OttSourceSyncService

    return _run(lambda db: OttSourceSyncService(db, "justwatch").sync())


@celery_app.task(name="operations.notifications")
def notifications():
    def run(db):
        from app.services.notification_service import NotificationService

        open_issues = (
            db.query(DataQualityIssue)
            .filter(
                DataQualityIssue.resolved_at.is_(None),
                DataQualityIssue.severity.in_(["high", "critical"]),
            )
            .count()
        )
        failed = db.query(OttEvidence).filter_by(status="FAILED").count()
        return NotificationService(db).notify(
            f"Daily health summary: {open_issues} serious data issues; {failed} failed OTT research items",
            "info",
            "daily-health-summary",
            1380,
        )

    return _run(run)


@celery_app.task(
    name="operations.movie_requests",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_requests():
    from app.services.movie_requests import MovieRequestAutomationService

    return _run(lambda db: MovieRequestAutomationService(db).maintain())


@celery_app.task(name="operations.cleanup")
def cleanup():
    def run(db):
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        notifications_deleted = (
            db.query(NotificationLog)
            .filter(NotificationLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        issues_deleted = (
            db.query(DataQualityIssue)
            .filter(
                DataQualityIssue.resolved_at.is_not(None),
                DataQualityIssue.resolved_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"notifications": notifications_deleted, "issues": issues_deleted}

    return _run(run)


def _ott_research_batch(db):
    provider = configured_ott_provider()
    service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
    if not getattr(provider, "configured", False):
        return {"configured": False, "processed": 0, "complete": False}
    now = datetime.now(timezone.utc)
    usage = ResearchUsageService(db)
    daily = usage.daily_snapshot(now)
    monthly = (
        usage.monthly_snapshot(now) if getattr(provider, "is_tavily", False) else None
    )
    available_slots = daily["remaining"]
    if monthly is not None and monthly["remaining"] <= 0:
        return {
            "configured": True,
            "processed": 0,
            "queries": 0,
            "complete": False,
            "stopped": "monthly_free_budget_exhausted",
            "daily": daily,
            "monthly": monthly,
        }
    if available_slots <= 0:
        return {
            "configured": True,
            "processed": 0,
            "queries": 0,
            "complete": False,
            "stopped": "daily_movie_limit_reached",
            "daily": daily,
            "monthly": monthly,
        }
    requested = exists(
        select(MovieRequest.id).where(
            func.lower(MovieRequest.movie_name) == func.lower(Movie.title),
            MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]),
        )
    )
    queue = (
        db.query(OttEvidence)
        .join(Movie, Movie.id == OttEvidence.movie_id)
        .filter(
            Movie.ott_research_eligibility == "ELIGIBLE",
            OttEvidence.source_url.is_(None),
            OttEvidence.status.in_(
                ["UNKNOWN", "QUEUED", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "FAILED"]
            ),
            OttEvidence.next_check <= now,
        )
        .order_by(
            case((requested, 0), else_=1),
            Movie.theatrical_release_date.desc(),
            Movie.popularity.desc(),
            OttEvidence.next_check,
        )
        .limit(available_slots)
        .all()
    )
    processed = 0
    queries_used = 0
    for item in queue:
        movie = db.get(Movie, item.movie_id)
        _, eligibility, _ = ReleaseStatusService(db).classify_movie(movie, now=now)
        if eligibility.code != "ELIGIBLE":
            db.commit()
            continue
        if (
            getattr(provider, "is_tavily", False)
            and usage.monthly_snapshot(now)["remaining"] <= 0
        ):
            break
        if not usage.reserve_daily_movie(now):
            break
        item.status = "RESEARCHING"
        item.attempts += 1
        maximum = settings.TAVILY_MAX_QUERIES_PER_MOVIE
        if getattr(provider, "is_tavily", False):
            monthly = usage.monthly_snapshot(now)
            maximum = min(maximum, monthly["remaining"])

            def reserve_query():
                return usage.reserve_tavily_query(now)
        else:
            reserve_query = None
        try:
            results = (
                provider.search(movie, max_queries=maximum, before_query=reserve_query)
                if movie and maximum > 0
                else []
            )
        except Exception as exc:
            item.status = "FAILED"
            item.last_checked = now
            item.next_check = service.next_check_for(
                item.status,
                attempts=item.attempts,
                theatrical_date=movie.theatrical_release_date if movie else None,
            )
            item.notes = sanitize_error(exc)
            processed += 1
            log.warning(
                "OTT provider failed for movie_id=%s; retry scheduled: %s",
                item.movie_id,
                item.notes,
            )
            db.commit()
            continue
        item_queries = max(0, getattr(provider, "last_query_count", 0))
        queries_used += item_queries
        if getattr(provider, "is_tavily", False) and item_queries == 0:
            item.status = "QUEUED"
            item.next_check = now + timedelta(days=1)
            db.commit()
            break
        if not results:
            item.status = "NOT_FOUND"
            item.last_checked = now
            item.next_check = service.next_check_for(
                item.status,
                attempts=item.attempts,
                theatrical_date=movie.theatrical_release_date if movie else None,
            )
            processed += 1
            continue
        evaluated = "UNKNOWN"
        for result in results[:3]:
            rank, score = source_rank(result.get("url"))
            inspected = result if result.get("inspected") else None
            if inspected is None:
                try:
                    inspected = inspect_source(movie, result)
                except Exception as exc:
                    log.info(
                        "OTT source inspection failed movie_id=%s source=%s: %s",
                        item.movie_id,
                        result.get("url"),
                        sanitize_error(exc),
                    )
            evidence_result = inspected or result
            evidence_rank = evidence_result.get("source_type") or rank
            evidence_score = evidence_result.get("confidence", score)
            parsed_date = evidence_result.get("release_date")
            try:
                parsed_date = date.fromisoformat(parsed_date) if parsed_date else None
            except (TypeError, ValueError):
                parsed_date = None
            published = evidence_result.get("published_date")
            try:
                published = date.fromisoformat(published[:10]) if published else None
            except (TypeError, ValueError):
                published = None
            evidence = service.record_evidence(
                item.movie_id,
                platform=evidence_result.get("platform"),
                release_date=parsed_date,
                source_url=evidence_result.get("url"),
                source_title=evidence_result.get("title"),
                source_published_at=published,
                summary=evidence_result.get("evidence_summary")
                or evidence_result.get("snippet"),
                confidence=evidence_score,
                source_type=evidence_rank,
                source_name=evidence_result.get("source_name"),
                country=evidence_result.get("country") or "UNKNOWN",
                inspected=bool(inspected),
            )
            if evidence.status == "CONFIRMED":
                evaluated = "CONFIRMED"
            elif evidence.status in {"CONFLICTING", "NEEDS_REVIEW"}:
                evaluated = evidence.status
            elif evaluated == "UNKNOWN":
                evaluated = "POSSIBLE"
        item.status = evaluated
        item.last_checked = now
        item.next_check = service.next_check_for(
            item.status,
            attempts=item.attempts,
            theatrical_date=movie.theatrical_release_date if movie else None,
        )
        item.notes = (
            f"sources_discovered={min(len(results), 3)}; queries={item_queries}"
        )
        log.info(
            "OTT researched movie_id=%s status=%s sources=%s queries=%s",
            item.movie_id,
            item.status,
            min(len(results), 3),
            item_queries,
        )
        processed += 1
    db.commit()
    daily = usage.daily_snapshot(now)
    monthly = (
        usage.monthly_snapshot(now) if getattr(provider, "is_tavily", False) else None
    )
    return {
        "configured": True,
        "processed": processed,
        "queries": queries_used,
        "complete": not queue,
        "daily": daily,
        "monthly": monthly,
    }


@celery_app.task(
    name="operations.ott_research",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_research():
    return _run(_ott_research_batch)


@celery_app.task(
    name="operations.ott_intelligence_daily",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_intelligence_daily():
    from app.services.ott.pipeline import OTTIntelligencePipeline

    return _run(lambda db: OTTIntelligencePipeline(db).run_daily())


@celery_app.task(
    name="operations.ott_intelligence_movie",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_intelligence_movie(movie_id: int):
    from app.services.ott.intelligence import OTTIntelligenceService

    return _run(lambda db: OTTIntelligenceService(db).refresh_movie(movie_id))


@celery_app.task(
    name="operations.ott_intelligence_weekly",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_intelligence_weekly():
    from app.services.ott.pipeline import OTTIntelligencePipeline

    return _run(lambda db: OTTIntelligencePipeline(db).run_weekly())


@celery_app.task(
    name="operations.ott_web_research",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_web_research(limit: int = 30):
    """Bounded AI/web evidence pass; technical failures remain distinguishable."""
    from app.services.ott.web_research import WebOttResearchService

    return _run(lambda db: WebOttResearchService(db).run(limit=limit))


@celery_app.task(name="operations.ott_gold_set_evaluate")
def ott_gold_set_evaluate():
    from app.services.ott.gold_set import OttGoldSetService

    return _run(lambda db: OttGoldSetService(db).evaluate())


@celery_app.task(bind=True, name="operations.repair_orchestrator")
def repair_orchestrator(self):
    """Run accelerated stages sequentially so providers are never maxed concurrently."""

    def run(db):
        from app.services.backfill import (
            ImageBackfillService,
            IMDbBackfillService,
            MetadataBackfillService,
            OttQueueBackfillService,
            PersonBackfillService,
        )

        state = (
            db.query(OperationState)
            .filter_by(name="operations.repair_orchestrator")
            .first()
        )
        if not state:
            state = OperationState(
                name="operations.repair_orchestrator", status="RUNNING"
            )
            db.add(state)
            db.flush()
        stages = (
            MetadataBackfillService,
            PersonBackfillService,
            ImageBackfillService,
            IMDbBackfillService,
            OttQueueBackfillService,
        )
        if state.cursor < len(stages):
            result = stages[state.cursor](db).run()
            if result.get("complete") or result.get("configured") is False:
                state.cursor += 1
        elif state.cursor == 5:
            result = _ott_research_batch(db)
            if result.get("complete") or result.get("configured") is False:
                state.cursor = 6
                health = db.query(OperationState).filter_by(name="data_health").first()
                if health:
                    health.cursor = 0
                    health.processed_count = 0
        elif state.cursor == 6:
            result = DataHealthService(db).scan(250)
            if result.get("cycle_complete"):
                state.cursor = 7
        else:
            state.status = "COMPLETE"
            state.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {"complete": True, "stage": "complete"}
        state.status = "RUNNING"
        state.processed_count += int(result.get("processed", result.get("scanned", 0)))
        state.last_success_at = datetime.now(timezone.utc)
        db.commit()
        return {"complete": False, "stage": state.cursor, "result": result}

    result = _run(run)
    if not result["complete"]:
        self.apply_async(countdown=3)
    return result
