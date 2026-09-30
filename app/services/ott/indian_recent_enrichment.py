"""High-yield, checkpointed OTT enrichment for recent Indian-language films."""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import case, exists, extract, func, or_
from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.operations import BackfillRecord, DataQualityIssue, OperationState
from app.models.ott_availability import OttAvailability
from app.services.ott.targeted_date_enrichment import (
    CONFIRMED_OTT,
    CuratedReleaseIndex,
    TargetedOttDateEnrichmentService,
)
from app.services.ott_providers import normalize_platform


OPERATION = "operations.indian_recent_ott_enrichment"
LANGUAGES = ("ml", "ta", "te", "hi", "kn")
YEARS = tuple(range(2026, 2019, -1))


class IndianRecentOttEnrichmentService(TargetedOttDateEnrichmentService):
    """Run bounded year/language queues without touching older operations."""

    operation = OPERATION

    @staticmethod
    def _release_date():
        return func.coalesce(Movie.theatrical_release_date, Movie.release_date)

    @staticmethod
    def _verified_platform():
        return exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            func.upper(OttAvailability.verification_status).in_(CONFIRMED_OTT),
        )

    @staticmethod
    def _verified_date():
        return exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            func.upper(OttAvailability.verification_status).in_(CONFIRMED_OTT),
            OttAvailability.ott_release_date.is_not(None),
        )

    @staticmethod
    def _verified_url():
        return exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            func.upper(OttAvailability.verification_status).in_(CONFIRMED_OTT),
            OttAvailability.source_url.is_not(None),
            OttAvailability.source_url != "",
        )

    @staticmethod
    def _has_imdb():
        return exists().where(
            ExternalId.movie_id == Movie.id,
            func.lower(ExternalId.provider).in_(("imdb", "imdb_id")),
            ExternalId.external_id.is_not(None),
            ExternalId.external_id != "",
        )

    def _attempted(self):
        return exists().where(
            BackfillRecord.operation == self.operation,
            BackfillRecord.entity_type == "movie",
            BackfillRecord.entity_id == Movie.id,
        )

    def _group_filter(self, year: int, language: str):
        release = self._release_date()
        return (
            Movie.original_language == language,
            release.is_not(None),
            release <= date.today(),
            extract("year", release) == year,
            self._incomplete(),
        )

    def targets_for_group(self, year: int, language: str, limit: int = 500) -> list[Movie]:
        if year not in YEARS or language not in LANGUAGES:
            return []
        release = self._release_date()
        has_platform = self._verified_platform()
        has_date = self._verified_date()
        has_url = self._verified_url()
        priority = case(
            (has_platform & ~has_date, 1),
            (has_platform & ~has_url, 2),
            (
                ~has_platform
                & Movie.theatrical_release_date.is_not(None)
                & Movie.tmdb_id.is_not(None)
                & self._has_imdb(),
                3,
            ),
            else_=4,
        )
        return (
            self.db.query(Movie)
            .filter(*self._group_filter(year, language), ~self._attempted())
            .order_by(
                priority,
                Movie.popularity.desc().nullslast(),
                release.desc(),
                Movie.id,
            )
            .limit(max(1, min(int(limit), 500)))
            .all()
        )

    def queue_counts(self) -> list[dict]:
        rows: list[dict] = []
        for year in YEARS:
            for language in LANGUAGES:
                base = self.db.query(func.count(Movie.id)).filter(
                    *self._group_filter(year, language), ~self._attempted()
                )
                has_platform = self._verified_platform()
                has_date = self._verified_date()
                rows.append(
                    {
                        "year": year,
                        "language": language,
                        "known_platform_date_missing": base.filter(
                            has_platform, ~has_date
                        ).scalar()
                        or 0,
                        "no_platform": base.filter(~has_platform).scalar() or 0,
                        "remaining": base.scalar() or 0,
                    }
                )
        return rows

    def _group_remaining(self, year: int, language: str) -> int:
        return (
            self.db.query(func.count(Movie.id))
            .filter(*self._group_filter(year, language), ~self._attempted())
            .scalar()
            or 0
        )

    def run_group_batch(
        self, year: int, language: str, batch_size: int = 500, batch_number: int | None = None
    ) -> dict:
        state = self._state()
        details = dict(state.details or {})
        if batch_number is None:
            batch_number = int(details.get("batches_completed") or 0) + 1
        targets = self.targets_for_group(year, language, batch_size)
        state.total_count = sum(row["remaining"] for row in self.queue_counts()) + int(
            state.processed_count or 0
        )
        state.status = "RUNNING"
        state.last_error = None
        state.details = details | {"current_year": year, "current_language": language}
        self.db.commit()

        index = CuratedReleaseIndex()
        index.load({language})
        report = {
            "batch": batch_number,
            "year": year,
            "language": language,
            "selected": len(targets),
            "processed": 0,
            "platforms_added": 0,
            "ott_dates_added": 0,
            "legal_urls_added": 0,
            "confirmed_existing": 0,
            "no_result": 0,
            "needs_review": 0,
            "conflicts": 0,
            "source_failures": len(index.failures),
            "errors": 0,
        }
        for item_number, movie in enumerate(targets, 1):
            before = self._snapshot(movie.id)
            existing_platform = ",".join(sorted(before["platforms"])) or "NONE"
            record = self._record(movie.id)
            record.status = "RUNNING"
            record.attempts = (record.attempts or 0) + 1
            record.last_attempt_at = datetime.now(timezone.utc)
            record.last_error = None
            self.db.commit()
            matched_sources: list[str] = []
            status = "NO_RESULT"
            try:
                for entry in index.matches(movie):
                    if self._save_entry(movie, entry):
                        matched_sources.append(entry.source_url)
                after = self._snapshot(movie.id)
                has_conflict = self.db.query(DataQualityIssue.id).filter_by(
                    movie_id=movie.id, issue_type="ott_conflicting", resolved_at=None
                ).first() is not None
                platform_delta = len(after["platforms"] - before["platforms"])
                date_delta = len(after["dates"] - before["dates"])
                url_delta = len(after["urls"] - before["urls"])
                report["platforms_added"] += platform_delta
                report["ott_dates_added"] += date_delta
                report["legal_urls_added"] += url_delta
                if has_conflict:
                    status = "CONFLICTING"
                    report["conflicts"] += 1
                    record.status, record.last_error = "DEFERRED", "CONFLICTING_EVIDENCE"
                elif after["confirmed_date"]:
                    status = "CONFIRMED" if not before["confirmed_date"] else "CONFIRMED_EXISTING"
                    report["confirmed_existing"] += int(before["confirmed_date"])
                    record.status, record.last_error = "DONE", None
                elif matched_sources:
                    status = "NEEDS_REVIEW"
                    report["needs_review"] += 1
                    record.status, record.last_error = "DEFERRED", "SINGLE_SOURCE_OR_UNCONFIRMED_DATE"
                else:
                    report["no_result"] += 1
                    record.status, record.last_error = "DEFERRED", "NO_RESULT"
                state = self._state()
                state.cursor = movie.id
                state.processed_count = (state.processed_count or 0) + 1
                report["processed"] += 1
                self.db.commit()
                release = movie.theatrical_release_date or movie.release_date
                confidence = "HIGH" if after["confirmed_date"] else ("MEDIUM" if matched_sources else "NONE")
                print(
                    f"BATCH: {batch_number} ITEM: {item_number}/{len(targets)} TITLE: {movie.title} "
                    f"YEAR: {release.year if release else ''} LANGUAGE: {language} "
                    f"EXISTING_PLATFORM: {existing_platform} "
                    f"PLATFORM_RESULT: {','.join(sorted(after['platforms'])) or 'NONE'} "
                    f"OTT_DATE_RESULT: {'CONFIRMED' if after['confirmed_date'] else 'NULL'} "
                    f"LEGAL_URL_RESULT: {'PRESENT' if after['urls'] else 'NULL'} "
                    f"SOURCE: {','.join(sorted({urlparse(url).hostname or '' for url in matched_sources})) or 'NONE'} "
                    f"CONFIDENCE: {confidence} STATUS: {status}",
                    flush=True,
                )
            except Exception as exc:
                self.db.rollback()
                record = self._record(movie.id)
                record.status = "FAILED"
                record.attempts = max(record.attempts or 1, 1)
                record.last_attempt_at = datetime.now(timezone.utc)
                record.last_error = sanitize_error(exc)
                report["errors"] += 1
                report["processed"] += 1
                state = self._state()
                state.cursor = movie.id
                state.processed_count = (state.processed_count or 0) + 1
                self.db.commit()

        processed = report["processed"]
        report["ott_date_hit_rate"] = round(100 * report["ott_dates_added"] / processed, 2) if processed else 0.0
        report["platform_hit_rate"] = round(100 * report["platforms_added"] / processed, 2) if processed else 0.0
        report["legal_url_hit_rate"] = round(100 * report["legal_urls_added"] / processed, 2) if processed else 0.0
        report["remaining_in_group"] = self._group_remaining(year, language)

        state = self._state()
        details = dict(state.details or {})
        cumulative = dict(details.get("cumulative") or {})
        for key in (
            "processed", "platforms_added", "ott_dates_added", "legal_urls_added",
            "confirmed_existing", "no_result", "needs_review", "conflicts", "errors",
        ):
            cumulative[key] = int(cumulative.get(key) or 0) + int(report[key])
        history = list(details.get("batch_history") or [])
        history.append(report)
        details.update(
            {
                "batch_size": batch_size,
                "batches_completed": max(int(details.get("batches_completed") or 0), batch_number),
                "last_batch": report,
                "batch_history": history,
                "cumulative": cumulative,
                "source_failures": index.failures,
                "current_year": year,
                "current_language": language,
            }
        )
        state.details = details
        state.status = "IDLE"
        state.last_success_at = datetime.now(timezone.utc)
        self.db.commit()
        return report | {"cumulative": cumulative}
