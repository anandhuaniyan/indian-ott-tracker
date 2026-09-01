"""Identifier-first, checkpointed discovery of newly released Indian movies."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from sqlalchemy import extract, func, or_
from sqlalchemy.orm import Session
from text_unidecode import unidecode

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.discovery import MovieDiscoveryCandidate, MovieDiscoveryRun
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.operations import OperationState, OttSourceRelease
from app.services.movie_metadata_service import MovieMetadataService
from app.services.notification_service import NotificationService
from app.services.operations import OttResearchService
from app.services.ott_source_sync import OttSourceSyncService
from app.services.release_status import ReleaseStatusService
from app.services.tmdb.movie_service import TMDbMovieService


LANGUAGES = ("ml", "ta", "te", "hi", "kn")
LANGUAGE_NAMES = {
    "ml": "Malayalam",
    "ta": "Tamil",
    "te": "Telugu",
    "hi": "Hindi",
    "kn": "Kannada",
}


def _site_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.SITE_TIMEZONE)
    except (KeyError, ValueError):
        return ZoneInfo("Asia/Singapore")


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalized_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unidecode(value or "").casefold()).strip()


def next_regular_discovery(now: datetime | None = None) -> datetime:
    """Return the next 08:00/20:00 instant in the configured site timezone."""
    zone = _site_timezone()
    local_now = (now or datetime.now(timezone.utc)).astimezone(zone)
    for hour in (8, 20):
        candidate = datetime.combine(local_now.date(), time(hour), zone)
        if candidate > local_now:
            return candidate
    return datetime.combine(local_now.date() + timedelta(days=1), time(8), zone)


class MovieDiscoveryService:
    """Run an isolated language/source scan and retain all decisions."""

    operation = "movies.discovery"

    def __init__(self, db: Session, tmdb: TMDbMovieService | None = None):
        self.db = db
        self.tmdb = tmdb or TMDbMovieService()

    def run_regular(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        local_today = now.astimezone(_site_timezone()).date()
        return self.run(
            window_start=local_today - timedelta(days=settings.MOVIE_DISCOVERY_REGULAR_PAST_DAYS),
            window_end=local_today + timedelta(days=settings.MOVIE_DISCOVERY_REGULAR_FUTURE_DAYS),
            run_type="REGULAR",
            now=now,
        )

    def run_weekly(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        local_today = now.astimezone(_site_timezone()).date()
        return self.run(
            window_start=local_today - timedelta(days=settings.MOVIE_DISCOVERY_WEEKLY_PAST_DAYS),
            window_end=local_today + timedelta(days=settings.MOVIE_DISCOVERY_WEEKLY_FUTURE_DAYS),
            run_type="WEEKLY",
            now=now,
        )

    def run(
        self,
        *,
        window_start: date,
        window_end: date,
        run_type: str = "MANUAL",
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        local_hour = now.astimezone(_site_timezone()).hour
        # Scheduled jobs normally begin at exactly 08:00 or 20:00. A small
        # window tolerates worker delays without mislabelling an operator's
        # daytime manual run as the evening schedule.
        slot = "MORNING" if 7 <= local_hour < 10 else "EVENING" if 19 <= local_hour < 22 else "AD_HOC"
        run = MovieDiscoveryRun(
            run_type=run_type,
            slot=slot,
            status="RUNNING",
            started_at=now,
            window_start=window_start,
            window_end=window_end,
            languages=list(LANGUAGES),
            language_stats={},
            source_stats={},
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        self._update_state(run, now)

        language_stats: dict[str, dict] = {}
        failures: list[str] = []
        for language in LANGUAGES:
            try:
                language_stats[language] = self._scan_language(
                    run.id, language, window_start, window_end
                )
            except Exception as exc:
                self.db.rollback()
                safe = sanitize_error(exc)
                failures.append(f"{language}: {safe}")
                language_stats[language] = {
                    "language": LANGUAGE_NAMES[language],
                    "status": "FAILED",
                    "discovered": 0,
                    "existing": 0,
                    "imported": 0,
                    "needs_review": 0,
                    "failed": 1,
                    "error": safe,
                }
            self._checkpoint(run.id, language_stats=language_stats)

        failures.extend(
            f"{language}: {item.get('error') or str(item.get('failed', 0)) + ' candidate failures'}"
            for language, item in language_stats.items()
            if item.get("status") != "COMPLETE"
        )

        source_stats = self._scan_configured_sources(run.id, window_start, window_end)
        failures.extend(
            f"{name}: {item.get('error', 'source sync failed')}"
            for name, item in source_stats.items()
            if item.get("status") == "FAILED"
        )
        successful_languages = sum(item.get("status") != "FAILED" for item in language_stats.values())
        run = self.db.get(MovieDiscoveryRun, run.id)
        run.language_stats = language_stats
        run.source_stats = source_stats
        run.completed_at = datetime.now(timezone.utc)
        run.status = "FAILED" if not successful_languages else "PARTIAL" if failures else "COMPLETE"
        run.last_error = "; ".join(failures)[:4000] or None
        self._recount(run)
        self.db.commit()
        self._update_state(run, run.completed_at)
        if run.status != "COMPLETE":
            self._notify_failure(run)
        return self.serialize_run(run)

    def _scan_language(
        self, run_id: int, language: str, window_start: date, window_end: date
    ) -> dict:
        stats = {
            "language": LANGUAGE_NAMES[language],
            "status": "COMPLETE",
            "pages": 0,
            "discovered": 0,
            "existing": 0,
            "imported": 0,
            "needs_review": 0,
            "failed": 0,
        }
        seen: set[int] = set()
        max_pages = settings.MOVIE_DISCOVERY_MAX_PAGES_PER_LANGUAGE
        page = 1
        while page <= max_pages:
            try:
                payload = self.tmdb.discover_movies_by_language_and_date_range(
                    language, window_start, window_end, page=page
                )
            except Exception as exc:
                stats["status"] = "FAILED"
                stats["failed"] += 1
                stats["error"] = sanitize_error(exc)
                return stats
            stats["pages"] += 1
            items = payload.get("results") or []
            for item in items:
                tmdb_id = item.get("id")
                if not isinstance(tmdb_id, int) or tmdb_id in seen:
                    continue
                seen.add(tmdb_id)
                result = self._process_tmdb_candidate(run_id, language, item)
                stats["discovered"] += 1
                key = {
                    "EXISTING": "existing",
                    "IMPORTED": "imported",
                    "NEEDS_REVIEW": "needs_review",
                    "FAILED": "failed",
                }.get(result)
                if key:
                    stats[key] += 1
            total_pages = min(int(payload.get("total_pages") or page), max_pages)
            if not items or page >= total_pages:
                break
            page += 1
        if stats["failed"]:
            stats["status"] = "PARTIAL"
        return stats

    def _process_tmdb_candidate(self, run_id: int, language: str, item: dict) -> str:
        now = datetime.now(timezone.utc)
        tmdb_id = int(item["id"])
        candidate = self._candidate(
            run_id=run_id,
            source="tmdb",
            external_key=str(tmdb_id),
            title=str(item.get("title") or item.get("original_title") or f"TMDB {tmdb_id}"),
            original_title=item.get("original_title"),
            language=language,
            release_date=_parse_date(item.get("release_date")),
            tmdb_id=tmdb_id,
            now=now,
        )
        if candidate.status in {"IGNORED", "DUPLICATE", "WRONG_LANGUAGE", "TV_SERIES"}:
            return "FILTERED"
        if candidate.status == "EXISTING" and candidate.matched_movie_id:
            return "EXISTING"
        existing = self.db.query(Movie).filter_by(tmdb_id=tmdb_id).first()
        if existing:
            self._mark(candidate, "EXISTING", existing.id, 100, "TMDB ID")
            return "EXISTING"
        try:
            details = self.tmdb.get_rich_movie_details(tmdb_id)
            if details.get("adult"):
                self._mark(candidate, "FILTERED", None, 100, "Adult title excluded")
                return "FILTERED"
            actual_language = str(details.get("original_language") or language).lower()
            candidate.language = actual_language
            candidate.title = str(details.get("title") or candidate.title)
            candidate.original_title = details.get("original_title") or candidate.original_title
            candidate.release_date = _parse_date(details.get("release_date")) or candidate.release_date
            imdb_id = str((details.get("external_ids") or {}).get("imdb_id") or "").strip() or None
            candidate.imdb_id = imdb_id
            if imdb_id:
                imdb_match = (
                    self.db.query(Movie)
                    .join(ExternalId, ExternalId.movie_id == Movie.id)
                    .filter(func.lower(ExternalId.provider) == "imdb", ExternalId.external_id == imdb_id)
                    .first()
                )
                if imdb_match:
                    self._mark(candidate, "EXISTING", imdb_match.id, 100, "IMDb ID")
                    return "EXISTING"
            title_match = self._title_identity_match(candidate)
            if title_match:
                self._mark(
                    candidate,
                    "NEEDS_REVIEW",
                    title_match.id,
                    85,
                    "Normalized title/year/language matches a different TMDB identity",
                )
                return "NEEDS_REVIEW"
            movie = Movie(
                tmdb_id=tmdb_id,
                title=candidate.title,
                original_title=candidate.original_title,
                overview=details.get("overview"),
                release_date=candidate.release_date,
                poster_path=details.get("poster_path"),
                backdrop_path=details.get("backdrop_path"),
                popularity=details.get("popularity"),
                vote_average=details.get("vote_average"),
                vote_count=details.get("vote_count"),
                original_language=actual_language,
                adult=False,
            )
            self.db.add(movie)
            self.db.flush()
            MovieMetadataService(self.db).enrich_movie(movie, payload=details)
            ReleaseStatusService(self.db).classify_movie(movie)
            OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD).queue_movie(movie.id)
            self.db.commit()
            candidate = self.db.get(MovieDiscoveryCandidate, candidate.id)
            self._mark(candidate, "IMPORTED", movie.id, 100, "New TMDB movie identity")
            self._enqueue_enrichment(movie.id)
            return "IMPORTED"
        except Exception as exc:
            self.db.rollback()
            candidate = self.db.query(MovieDiscoveryCandidate).filter_by(
                source="tmdb", external_key=str(tmdb_id)
            ).first()
            candidate.latest_run_id = run_id
            candidate.last_seen_at = now
            candidate.status = "FAILED"
            candidate.last_error = sanitize_error(exc)
            self.db.commit()
            return "FAILED"

    def _title_identity_match(self, candidate: MovieDiscoveryCandidate) -> Movie | None:
        if not candidate.release_date or not candidate.language:
            return None
        rows = (
            self.db.query(Movie)
            .filter(
                Movie.original_language == candidate.language,
                extract("year", Movie.release_date) == candidate.release_date.year,
                or_(Movie.title.is_not(None), Movie.original_title.is_not(None)),
            )
            .limit(500)
            .all()
        )
        names = {_normalized_title(candidate.title), _normalized_title(candidate.original_title)} - {""}
        matches = [
            movie
            for movie in rows
            if names
            & {_normalized_title(movie.title), _normalized_title(movie.original_title)}
        ]
        return matches[0] if len(matches) == 1 else None

    def _candidate(self, *, run_id: int, source: str, external_key: str, title: str,
                   original_title: str | None, language: str | None, release_date: date | None,
                   tmdb_id: int | None, now: datetime) -> MovieDiscoveryCandidate:
        candidate = self.db.query(MovieDiscoveryCandidate).filter_by(
            source=source, external_key=external_key
        ).first()
        if not candidate:
            candidate = MovieDiscoveryCandidate(
                source=source,
                external_key=external_key,
                title=title,
                first_discovered_at=now,
                last_seen_at=now,
            )
            self.db.add(candidate)
        candidate.latest_run_id = run_id
        candidate.tmdb_id = tmdb_id
        candidate.title = title
        candidate.original_title = original_title
        candidate.language = language
        candidate.release_date = release_date
        candidate.last_seen_at = now
        candidate.last_error = None
        self.db.commit()
        self.db.refresh(candidate)
        return candidate

    def _mark(self, candidate: MovieDiscoveryCandidate, status: str, movie_id: int | None,
              confidence: float, reason: str) -> None:
        candidate.status = status
        candidate.matched_movie_id = movie_id
        candidate.match_confidence = confidence
        candidate.match_reason = reason
        candidate.last_error = None
        self.db.commit()

    def _scan_configured_sources(self, run_id: int, start: date, end: date) -> dict:
        source_stats: dict[str, dict] = {}
        for source in ("ottplay", "justwatch"):
            service = OttSourceSyncService(self.db, source)
            if not service.enabled:
                source_stats[source] = {"status": "DISABLED", "processed": 0}
                continue
            try:
                result = service.sync()
                if result.get("failed"):
                    source_stats[source] = {"status": "FAILED", "error": result.get("last_error")}
                    continue
                mirrored = self._mirror_source_review(run_id, source, start, end)
                source_stats[source] = {
                    "status": "COMPLETE",
                    "processed": result.get("processed", 0),
                    "needs_review": mirrored,
                }
            except Exception as exc:
                self.db.rollback()
                source_stats[source] = {"status": "FAILED", "error": sanitize_error(exc)}
        return source_stats

    def _mirror_source_review(self, run_id: int, source: str, start: date, end: date) -> int:
        rows = self.db.query(OttSourceRelease).filter(
            OttSourceRelease.source == source,
            OttSourceRelease.status == "UNMATCHED",
            or_(OttSourceRelease.release_date.is_(None), OttSourceRelease.release_date.between(start, end)),
        ).limit(settings.OTT_SOURCE_SYNC_BATCH_SIZE).all()
        for row in rows:
            candidate = self._candidate(
                run_id=run_id,
                source=source,
                external_key=row.external_key,
                title=row.title,
                original_title=row.original_title,
                language=row.language,
                release_date=row.release_date,
                tmdb_id=None,
                now=datetime.now(timezone.utc),
            )
            self._mark(candidate, "NEEDS_REVIEW", None, 0, "Configured release source could not identify a local movie")
        return len(rows)

    @staticmethod
    def _enqueue_enrichment(movie_id: int) -> None:
        from app.workers.celery_app import celery_app

        for task in ("repair.movie", "operations.ott_intelligence_movie"):
            try:
                celery_app.send_task(task, args=[movie_id])
            except Exception:
                # The import and its core metadata transaction are authoritative.
                # Beat/repair orchestration will pick up enrichment gaps if the
                # broker is temporarily unavailable.
                continue

    def _checkpoint(self, run_id: int, *, language_stats: dict) -> None:
        run = self.db.get(MovieDiscoveryRun, run_id)
        run.language_stats = language_stats
        self._recount(run)
        self.db.commit()

    def _recount(self, run: MovieDiscoveryRun) -> None:
        stats = list((run.language_stats or {}).values())
        source_stats = list((run.source_stats or {}).values())
        run.candidates_discovered = sum(int(item.get("discovered", 0)) for item in stats) + sum(int(item.get("processed", 0)) for item in source_stats)
        run.already_existing = sum(int(item.get("existing", 0)) for item in stats)
        run.new_movies_imported = sum(int(item.get("imported", 0)) for item in stats)
        run.needs_review = sum(int(item.get("needs_review", 0)) for item in stats + source_stats)
        run.failed = sum(int(item.get("failed", 0)) for item in stats) + sum(item.get("status") == "FAILED" for item in source_stats)

    def _update_state(self, run: MovieDiscoveryRun, now: datetime) -> None:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not state:
            state = OperationState(name=self.operation)
            self.db.add(state)
        state.status = run.status
        state.processed_count = (state.processed_count or 0) + run.candidates_discovered
        state.total_count = run.candidates_discovered
        state.cursor = run.id
        state.last_error = run.last_error
        state.details = {
            "run_id": run.id,
            "run_type": run.run_type,
            "slot": run.slot,
            "window_start": run.window_start.isoformat(),
            "window_end": run.window_end.isoformat(),
            "counts": {
                "discovered": run.candidates_discovered,
                "existing": run.already_existing,
                "imported": run.new_movies_imported,
                "needs_review": run.needs_review,
                "failed": run.failed,
            },
            "next_run": next_regular_discovery(now).isoformat(),
        }
        if run.status == "COMPLETE":
            state.last_success_at = now
            state.completed_at = now
        elif run.status in {"FAILED", "PARTIAL"}:
            state.last_failure_at = now
        self.db.commit()

    def _notify_failure(self, run: MovieDiscoveryRun) -> None:
        recent = (
            self.db.query(MovieDiscoveryRun)
            .filter(MovieDiscoveryRun.run_type == "REGULAR")
            .order_by(MovieDiscoveryRun.started_at.desc())
            .limit(2)
            .all()
        )
        both_failed = len(recent) == 2 and all(item.status == "FAILED" for item in recent)
        severity = "critical" if both_failed else "high" if run.status == "FAILED" else "warning"
        suffix = " Both scheduled runs failed; discovery is stale." if both_failed else ""
        NotificationService(self.db).notify(
            f"Movie discovery {run.slot.lower()} run {run.status.lower()}: {run.last_error or 'one or more sources failed'}.{suffix}",
            severity,
            f"movie-discovery:{run.started_at.astimezone(_site_timezone()).date()}:{run.slot}",
            cooldown_minutes=600,
        )

    @staticmethod
    def serialize_run(run: MovieDiscoveryRun) -> dict:
        return {
            "id": run.id,
            "run_type": run.run_type,
            "slot": run.slot,
            "status": run.status,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "window_start": run.window_start,
            "window_end": run.window_end,
            "candidates_discovered": run.candidates_discovered,
            "already_existing": run.already_existing,
            "new_movies_imported": run.new_movies_imported,
            "needs_review": run.needs_review,
            "failed": run.failed,
            "language_stats": run.language_stats or {},
            "source_stats": run.source_stats or {},
            "last_error": run.last_error,
        }
