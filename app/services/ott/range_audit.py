"""Restart-safe July--December 2026 OTT integrity audit.

This pass classifies every in-scope record without inventing facts. It repairs
only structurally invalid public relationships; uncertain facts are preserved
for administrators as NEEDS_REVIEW or NO_RELIABLE_RESULT.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.movie import Movie
from app.models.movie_metadata import MovieReleaseDate
from app.models.operations import BackfillRecord, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.ott_providers import normalize_platform


OPERATION = "operations.ott_range_audit_2026_h2"
START = date(2026, 7, 1)
END = date(2026, 12, 31)
VALID_WATCH_TYPES = {"subscription", "ads", "free"}
INVALID_PLATFORMS = {"", "platform tba", "tba", "unknown", "n/a", "na", "digital", "vod"}
TERMINAL = {"VERIFIED", "UNCHANGED", "CORRECTED", "NO_RELIABLE_RESULT", "NEEDS_REVIEW"}


def _primary_date():
    return func.coalesce(Movie.theatrical_release_date, Movie.release_date)


def _dated_release_in_range():
    return exists(select(MovieReleaseDate.id).where(
        MovieReleaseDate.movie_id == Movie.id,
        MovieReleaseDate.release_date.between(START, END),
    ))


def _dated_ott_in_range():
    return exists(select(OttAvailability.id).where(
        OttAvailability.movie_id == Movie.id,
        OttAvailability.ott_release_date.between(START, END),
    ))


def _dated_evidence_in_range():
    return exists(select(OttEvidence.id).where(
        OttEvidence.movie_id == Movie.id,
        OttEvidence.release_date.between(START, END),
    ))


def _suspicious_ott():
    return exists(select(OttAvailability.id).where(
        OttAvailability.movie_id == Movie.id,
        or_(
            OttAvailability.provider.is_(None),
            func.length(func.trim(OttAvailability.provider)) == 0,
            func.lower(func.trim(OttAvailability.provider)).in_(INVALID_PLATFORMS),
            OttAvailability.ott_release_date.is_(None),
            OttAvailability.verification_status.in_(("CONFLICTING", "NEEDS_REVIEW")),
        ),
    ))


def population_filter():
    return or_(
        _primary_date().between(START, END),
        _dated_release_in_range(),
        _dated_ott_in_range(),
        _dated_evidence_in_range(),
        _suspicious_ott(),
    )


class OttRangeAuditService:
    operation = OPERATION

    def __init__(self, db: Session):
        self.db = db

    def _state(self) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if state is None:
            state = OperationState(name=self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    def population(self) -> int:
        return int(self.db.query(func.count(Movie.id)).filter(population_filter()).scalar() or 0)

    def _targets(self, limit: int):
        done = exists().where(
            BackfillRecord.operation == self.operation,
            BackfillRecord.entity_type == "movie",
            BackfillRecord.entity_id == Movie.id,
            BackfillRecord.status == "DONE",
        )
        return (
            self.db.query(Movie)
            .options(
                selectinload(Movie.ott_availabilities),
                selectinload(Movie.release_dates),
            )
            .filter(population_filter(), ~done)
            .order_by(Movie.id)
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )

    def _record(self, movie_id: int) -> BackfillRecord:
        record = self.db.query(BackfillRecord).filter_by(
            operation=self.operation, entity_type="movie", entity_id=movie_id
        ).first()
        if record is None:
            record = BackfillRecord(
                operation=self.operation, entity_type="movie", entity_id=movie_id
            )
            self.db.add(record)
            self.db.flush()
        return record

    def _classify(self, movie: Movie) -> tuple[str, dict]:
        corrected = invalid = conflicts = 0
        valid_complete = []
        platform_only = []
        for row in movie.ott_availabilities:
            provider = normalize_platform(row.provider)
            invalid_provider = not provider or provider.casefold() in INVALID_PLATFORMS
            watch_type = (row.watch_type or "").casefold()
            if invalid_provider:
                invalid += 1
                if (row.status or "").casefold() in {"available", "confirmed", "announced", "upcoming", "released"}:
                    row.status = "unknown"
                    row.verification_status = "NEEDS_REVIEW"
                    corrected += 1
                continue
            if row.verification_status in {"CONFLICTING", "NEEDS_REVIEW"}:
                conflicts += 1
            if watch_type in VALID_WATCH_TYPES:
                if row.ott_release_date:
                    valid_complete.append(row)
                else:
                    platform_only.append(row)

        evidence_conflict = self.db.query(OttEvidence.id).filter(
            OttEvidence.movie_id == movie.id,
            OttEvidence.status.in_(("CONFLICTING", "NEEDS_REVIEW")),
            OttEvidence.rejected_at.is_(None),
        ).first() is not None
        conflicts += int(evidence_conflict)

        if corrected:
            terminal = "CORRECTED"
        elif conflicts:
            terminal = "NEEDS_REVIEW"
        elif valid_complete:
            terminal = "VERIFIED" if any(row.verification_status == "CONFIRMED" for row in valid_complete) else "UNCHANGED"
        elif platform_only:
            terminal = "NO_RELIABLE_RESULT"
        else:
            terminal = "NO_RELIABLE_RESULT"
        return terminal, {
            "corrected": corrected,
            "invalid": invalid,
            "conflicts": conflicts,
            "valid_complete": len(valid_complete),
            "platform_only": len(platform_only),
        }

    def run(self, batch_size: int = 1000) -> dict:
        state = self._state()
        state.total_count = self.population()
        state.status = "RUNNING"
        state.last_error = None
        self.db.commit()
        report = {key: 0 for key in (*TERMINAL, "SCANNED", "INVALID_RELATIONSHIPS")}
        targets = self._targets(batch_size)
        for movie in targets:
            terminal, facts = self._classify(movie)
            record = self._record(movie.id)
            record.status = "DONE"
            record.attempts = (record.attempts or 0) + 1
            record.last_attempt_at = datetime.now(timezone.utc)
            record.last_error = terminal
            report[terminal] += 1
            report["SCANNED"] += 1
            report["INVALID_RELATIONSHIPS"] += facts["invalid"]
            state.cursor = movie.id
            self.db.commit()

        completed = int(self.db.query(func.count(BackfillRecord.id)).filter_by(
            operation=self.operation, entity_type="movie", status="DONE"
        ).scalar() or 0)
        totals = dict(state.details or {})
        for key, value in report.items():
            totals[key] = int(totals.get(key) or 0) + int(value)
        state.details = totals
        state.processed_count = completed
        state.last_success_at = datetime.now(timezone.utc)
        remaining = bool(self._targets(1))
        state.status = "PAUSED" if remaining else "COMPLETE"
        state.completed_at = None if remaining else datetime.now(timezone.utc)
        self.db.commit()
        return {
            "operation": self.operation,
            "processed": report["SCANNED"],
            "processed_total": completed,
            "total": state.total_count,
            "remaining": max(0, state.total_count - completed),
            "status": state.status,
            "cumulative": totals,
        }
