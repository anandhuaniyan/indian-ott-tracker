"""Database-wide, resumable OTT completeness audit for the India catalogue.

The initial pass deliberately separates facts: TMDB/JustWatch can establish an
India platform observation, but it never invents an OTT premiere date. A
configured lawful web provider may add inspected date evidence for recent
movies; without one, the date stays unknown and is reported as such.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import and_, case, exists, extract, func, or_, select
from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.operations import BackfillRecord, MovieRequest, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import OttAvailabilityObservation
from app.services.languages import (
    ALL_PUBLIC_LANGUAGES,
    INDIAN_LANGUAGE_CODES,
    INTERNATIONAL_LANGUAGE_CODES,
)
from app.services.ott.intelligence import OTTIntelligenceService
from app.services.ott.web_research import WebOttResearchService


OPERATION = "operations.ott_catalogue_audit"
MIN_BULK_YEAR = 2011


def _primary_date():
    return func.coalesce(Movie.theatrical_release_date, Movie.release_date)


def _has_platform():
    return exists(
        select(OttAvailability.id).where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.provider.is_not(None),
            func.length(func.trim(OttAvailability.provider)) > 0,
        )
    )


def _has_date():
    return exists(
        select(OttAvailability.id).where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.ott_release_date.is_not(None),
        )
    )


def _was_researched():
    inspected_evidence = exists(
        select(OttEvidence.id).where(
            OttEvidence.movie_id == Movie.id,
            OttEvidence.source_url.is_not(None),
            OttEvidence.inspected_at.is_not(None),
        )
    )
    provider_observation = exists(
        select(OttAvailabilityObservation.id).where(
            OttAvailabilityObservation.movie_id == Movie.id
        )
    )
    return or_(inspected_evidence, provider_observation)


def _not_found_after_research():
    return exists(
        select(OttEvidence.id).where(
            OttEvidence.movie_id == Movie.id,
            OttEvidence.status == "NOT_FOUND",
            OttEvidence.last_checked.is_not(None),
        )
    )


def _needs_review():
    availability_review = exists(
        select(OttAvailability.id).where(
            OttAvailability.movie_id == Movie.id,
            OttAvailability.verification_status.in_(("CONFLICTING", "NEEDS_REVIEW")),
        )
    )
    evidence_review = exists(
        select(OttEvidence.id).where(
            OttEvidence.movie_id == Movie.id,
            OttEvidence.status.in_(("CONFLICTING", "NEEDS_REVIEW")),
            OttEvidence.rejected_at.is_(None),
        )
    )
    return or_(availability_review, evidence_review)


def _aggregate(db: Session, *predicates) -> dict:
    has_platform = _has_platform()
    has_date = _has_date()
    researched = _was_researched()
    not_found = _not_found_after_research()
    needs_review = _needs_review()
    row = (
        db.query(
            func.count(Movie.id),
            func.coalesce(func.sum(case((has_platform, 1), else_=0)), 0),
            func.coalesce(func.sum(case((has_date, 1), else_=0)), 0),
            func.coalesce(func.sum(case((and_(has_platform, ~has_date), 1), else_=0)), 0),
            func.coalesce(func.sum(case((and_(has_date, ~has_platform), 1), else_=0)), 0),
            func.coalesce(func.sum(case((researched, 1), else_=0)), 0),
            func.coalesce(func.sum(case((not_found, 1), else_=0)), 0),
            func.coalesce(func.sum(case((needs_review, 1), else_=0)), 0),
        )
        .filter(*predicates)
        .one()
    )
    total, platform, release_date, platform_no_date, date_no_platform, checked, no_verified, review = map(int, row)
    complete = min(platform, release_date)
    return {
        "movies": total,
        "platform_known": platform,
        "missing_platform": max(0, total - platform),
        "date_known": release_date,
        "missing_date": max(0, total - release_date),
        "platform_known_date_unknown": platform_no_date,
        "date_known_platform_unknown": date_no_platform,
        "complete": complete,
        "missing": max(0, total - complete),
        "researched": checked,
        "never_checked": max(0, total - checked),
        "no_verified_ott_found": no_verified,
        "needs_review": review,
        "research_coverage_percent": round(checked * 100 / total, 2) if total else 0.0,
        "complete_percent": round(complete * 100 / total, 2) if total else 0.0,
    }


def ott_completeness_metrics(db: Session) -> dict:
    """Return real database-wide totals with language and year breakdowns."""
    summary = _aggregate(db)
    by_language = []
    for code, label in ALL_PUBLIC_LANGUAGES:
        item = _aggregate(db, Movie.original_language == code)
        if item["movies"]:
            by_language.append({"code": code, "language": label, **item})

    years = [
        int(value)
        for (value,) in db.query(extract("year", _primary_date()))
        .filter(_primary_date().is_not(None))
        .distinct()
        .order_by(extract("year", _primary_date()).desc())
        .all()
        if value is not None
    ]
    by_year = [
        {"year": year, **_aggregate(db, extract("year", _primary_date()) == year)}
        for year in years
    ]
    return {"summary": summary, "by_language": by_language, "by_year": by_year}


class OttCatalogueAuditService:
    """Checkpoint every eligible movie while respecting provider budgets."""

    operation = OPERATION

    def __init__(self, db: Session):
        self.db = db
        self.intelligence = OTTIntelligenceService(db)
        self.web = WebOttResearchService(db)

    def _state(self) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not state:
            state = OperationState(name=self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    def _record(self, movie_id: int) -> BackfillRecord:
        record = self.db.query(BackfillRecord).filter_by(
            operation=self.operation, entity_type="movie", entity_id=movie_id
        ).first()
        if not record:
            record = BackfillRecord(
                operation=self.operation,
                entity_type="movie",
                entity_id=movie_id,
            )
            self.db.add(record)
            self.db.flush()
        return record

    def _targets(self, batch_size: int):
        completed = exists().where(
            BackfillRecord.operation == self.operation,
            BackfillRecord.entity_type == "movie",
            BackfillRecord.entity_id == Movie.id,
            BackfillRecord.status == "DONE",
        )
        requested = exists().where(
            MovieRequest.local_movie_id == Movie.id,
            MovieRequest.status.in_(("PENDING", "REVIEWING", "FOUND")),
        )
        year = extract("year", _primary_date())
        year_priority = case(
            (year == 2026, 0),
            (year == 2025, 1),
            (year == 2024, 2),
            (year.between(2020, 2023), 3),
            (year.between(2011, 2019), 4),
            else_=5,
        )
        language_priority = case(
            (Movie.original_language.in_(INDIAN_LANGUAGE_CODES), 0),
            (Movie.original_language.in_(INTERNATIONAL_LANGUAGE_CODES), 1),
            else_=2,
        )
        platform = _has_platform()
        release_date = _has_date()
        completeness_priority = case(
            (and_(platform, ~release_date), 0),
            (and_(release_date, ~platform), 1),
            else_=2,
        )
        return (
            self.db.query(Movie)
            .filter(
                _primary_date() >= date(MIN_BULK_YEAR, 1, 1),
                or_(~platform, ~release_date, _needs_review()),
                ~completed,
            )
            .order_by(
                case((requested, 0), else_=1),
                year_priority,
                language_priority,
                completeness_priority,
                Movie.popularity.desc().nullslast(),
                Movie.id,
            )
            .limit(batch_size)
            .all()
        )

    def run(self, batch_size: int = 100) -> dict:
        batch_size = max(1, min(int(batch_size), 100))
        state = self._state()
        previous_details = state.details or {}
        baseline = previous_details.get("baseline") or ott_completeness_metrics(self.db)["summary"]
        cumulative = dict(previous_details.get("cumulative") or {})
        eligible_total = self.db.query(func.count(Movie.id)).filter(
            _primary_date() >= date(MIN_BULK_YEAR, 1, 1),
            or_(~_has_platform(), ~_has_date(), _needs_review()),
        ).scalar() or 0
        state.total_count = int(eligible_total)
        state.status = "RUNNING"
        state.last_error = None
        state.completed_at = None
        self.db.commit()

        targets = self._targets(batch_size)
        report = {
            "processed": 0,
            "platforms_added": 0,
            "platform_relationships_added": 0,
            "dates_added": 0,
            "complete": 0,
            "platform_verified_date_unknown": 0,
            "no_verified_ott_found": 0,
            "needs_review": 0,
            "unknown": 0,
            "failed": 0,
            "web_provider_configured": bool(getattr(self.web.provider, "configured", False)),
            "stopped": None,
        }

        for candidate in targets:
            movie_id = candidate.id
            record = self._record(movie_id)
            record.status = "RUNNING"
            record.attempts += 1
            record.last_attempt_at = datetime.now(timezone.utc)
            record.last_error = None
            self.db.commit()
            before_platform = self.db.query(Movie.id).filter(Movie.id == movie_id, _has_platform()).first() is not None
            before_date = self.db.query(Movie.id).filter(Movie.id == movie_id, _has_date()).first() is not None
            before_relationships = self.db.query(func.count(OttAvailability.id)).filter(
                OttAvailability.movie_id == movie_id,
                func.upper(OttAvailability.country) == "IN",
            ).scalar() or 0
            try:
                result = self.intelligence.refresh_movie(
                    movie_id,
                    provider_names={"tmdb_justwatch"},
                )
                provider_status = result.get("providers", {}).get("tmdb_justwatch", {}).get("status")
                if provider_status in {"QUOTA_EXHAUSTED", "RATE_LIMITED"}:
                    record.status = "PENDING"
                    record.attempts = max(0, record.attempts - 1)
                    record.last_error = provider_status
                    report["stopped"] = provider_status
                    self.db.commit()
                    break
                if provider_status == "DISABLED":
                    record.status = "PENDING"
                    record.attempts = max(0, record.attempts - 1)
                    record.last_error = "TMDB_OTT_PROVIDER_NOT_CONFIGURED"
                    report["stopped"] = "NOT_CONFIGURED"
                    self.db.commit()
                    break
                if provider_status != "HEALTHY":
                    # A provider/circuit failure is not evidence about the
                    # movie. Pause the whole run and leave this candidate
                    # resumable instead of immediately cycling over the same
                    # failed batch.
                    record.status = "PENDING"
                    record.attempts = max(0, record.attempts - 1)
                    record.last_error = provider_status or "PROVIDER_FAILURE"
                    report["stopped"] = "PROVIDER_UNAVAILABLE"
                    self.db.commit()
                    break

                movie = self.db.get(Movie, movie_id)
                primary_release = movie.theatrical_release_date or movie.release_date
                movie_year = primary_release.year if primary_release else None
                if report["web_provider_configured"] and (movie_year or 0) >= 2024 and not before_date:
                    research_result = self.web.research_movie(movie_id, max_queries=3)
                    if research_result.get("error") == "QUOTA_EXCEEDED" or any(s.get("error") == "QUOTA_EXCEEDED" for s in research_result.get("sources", [])):
                        print("Tavily quota exceeded, skipping web research for this batch.")
                        report["web_provider_configured"] = False

                after_platform = self.db.query(Movie.id).filter(Movie.id == movie_id, _has_platform()).first() is not None
                after_date = self.db.query(Movie.id).filter(Movie.id == movie_id, _has_date()).first() is not None
                after_relationships = self.db.query(func.count(OttAvailability.id)).filter(
                    OttAvailability.movie_id == movie_id,
                    func.upper(OttAvailability.country) == "IN",
                ).scalar() or 0
                report["platforms_added"] += int(after_platform and not before_platform)
                report["platform_relationships_added"] += max(
                    0, int(after_relationships) - int(before_relationships)
                )
                report["dates_added"] += int(after_date and not before_date)
                if after_platform and after_date:
                    report["complete"] += 1
                elif after_platform:
                    report["platform_verified_date_unknown"] += 1
                elif self.db.query(Movie.id).filter(Movie.id == movie_id, _not_found_after_research()).first():
                    report["no_verified_ott_found"] += 1
                elif self.db.query(Movie.id).filter(Movie.id == movie_id, _needs_review()).first():
                    report["needs_review"] += 1
                else:
                    report["unknown"] += 1
                record.status = "DONE"
                record.last_error = None
                report["processed"] += 1
                state = self._state()
                state.cursor = movie_id
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                record = self._record(movie_id)
                record.status = "FAILED"
                record.last_error = sanitize_error(exc)
                report["failed"] += 1
                self.db.commit()

        completed = self.db.query(func.count(BackfillRecord.id)).filter_by(
            operation=self.operation,
            entity_type="movie",
            status="DONE",
        ).scalar() or 0
        state = self._state()
        state.processed_count = int(completed)
        state.last_success_at = datetime.now(timezone.utc)
        for key in (
            "processed",
            "platforms_added",
            "platform_relationships_added",
            "dates_added",
            "complete",
            "platform_verified_date_unknown",
            "no_verified_ott_found",
            "needs_review",
            "unknown",
        ):
            cumulative[key] = int(cumulative.get(key) or 0) + int(report.get(key) or 0)
        # Report currently failed entities, not repeated attempts against the
        # same entities. This keeps the resumable audit metric truthful.
        cumulative["failed"] = int(
            self.db.query(func.count(BackfillRecord.id)).filter_by(
                operation=self.operation,
                entity_type="movie",
                status="FAILED",
            ).scalar()
            or 0
        )
        after = ott_completeness_metrics(self.db)["summary"]
        state.details = {
            "baseline": baseline,
            "last_batch": report,
            "cumulative": cumulative,
            "after": after,
            "web_provider_configured": report["web_provider_configured"],
        }
        remaining = bool(self._targets(1))
        if report["stopped"]:
            state.status = "PAUSED"
            state.last_error = report["stopped"]
        elif remaining:
            state.status = "PAUSED"
            state.last_error = None
        else:
            state.status = "COMPLETE"
            state.completed_at = datetime.now(timezone.utc)
            state.last_error = None
        self.db.commit()
        return {
            "operation": self.operation,
            **report,
            "processed_total": state.processed_count,
            "total": state.total_count,
            "remaining": max(0, state.total_count - state.processed_count),
            "status": state.status,
        }
