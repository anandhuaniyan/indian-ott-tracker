"""Persistent AI research audit recording and non-blocking Discord delivery."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.ai_research_audit import (
    AIResearchAuditRun,
    AIResearchEvent,
    AIResearchMovieActivity,
    AIResearchSource,
)


SECRET_KEYS = {"authorization", "api_key", "apikey", "key", "token", "access_token", "password", "secret", "webhook"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seconds(started: datetime, completed: datetime) -> float:
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return max(0.0, round((completed - started).total_seconds(), 3))


def safe_source_url(value: str) -> str:
    """Retain a clickable URL while removing credentials and secret query fields."""
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        query = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in SECRET_KEYS
        ]
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ""))[:2000]
    except (TypeError, ValueError):
        return ""


def safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        result = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)[:100]
            if any(secret in key.lower() for secret in SECRET_KEYS):
                result[key] = "[redacted]"
            else:
                result[key] = safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [safe_value(item, depth=depth + 1) for item in list(value)[:100]]
    return sanitize_error(value, limit=1000)


class AIResearchDiscordNotifier:
    """Bounded Discord webhook client; callers must treat failures as non-fatal."""

    @property
    def configured(self) -> bool:
        return bool(
            settings.AI_RESEARCH_DISCORD_ENABLED
            and settings.AI_RESEARCH_DISCORD_WEBHOOK_URL
        )

    def send(self, content: str) -> bool:
        if not self.configured:
            return False
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                response = httpx.post(
                    settings.AI_RESEARCH_DISCORD_WEBHOOK_URL,
                    json={"content": content[:1900]},
                    timeout=max(1.0, min(settings.AI_RESEARCH_DISCORD_TIMEOUT_SECONDS, 10.0)),
                )
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return True
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    last_error = RuntimeError(f"Discord returned HTTP {response.status_code}")
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
        if last_error:
            raise last_error
        return False

    @staticmethod
    def run_started(run: AIResearchAuditRun) -> str:
        return "\n".join((
            "🤖 AI Research Run Started",
            f"Run: {run.run_id[:12]}",
            f"Movies: {run.movies_total}",
            f"Provider: {run.provider or 'Unknown'}",
            f"Model: {run.model or 'Unknown'}",
            f"Mode: {run.mode}",
            f"Admin: {settings.SITE_URL.rstrip('/')}/admin/ai-activity/runs/{run.run_id}",
        ))

    @staticmethod
    def run_completed(run: AIResearchAuditRun) -> str:
        runtime = f"{run.duration_seconds:.1f}s" if run.duration_seconds is not None else "Unknown"
        return "\n".join((
            "🤖 AI Research Run Complete" if run.status == "COMPLETED" else "⚠️ AI Research Run Failed",
            f"Run: {run.run_id[:12]}",
            f"Movies: {run.movies_processed}/{run.movies_total}",
            f"Successful: {run.successful}",
            f"Needs Review: {run.needs_review}",
            f"Conflicts: {run.conflicts}",
            f"No Reliable Result: {run.no_reliable_result}",
            f"Provider Failures: {run.provider_failures}",
            f"Sources inspected: {run.sources_inspected}",
            f"Database updates: {run.database_updates}",
            f"Mode: {run.mode}",
            f"Runtime: {runtime}",
            f"Admin: {settings.SITE_URL.rstrip('/')}/admin/ai-activity/runs/{run.run_id}",
        ))

    @staticmethod
    def movie_completed(movie: AIResearchMovieActivity, sources: list[AIResearchSource]) -> str:
        runtime = f"{movie.duration_seconds:.1f}s" if movie.duration_seconds is not None else "Unknown"
        platforms = ", ".join(movie.platforms_found) or "Not confirmed"
        dates = ", ".join(movie.dates_found) or "Not confirmed"
        evidence = [f"{item.domain}: {item.url}" for item in sources[:2]] or ["No accepted source"]
        if movie.error_type:
            heading = "⚠️ OTT AI Provider Failure"
        elif movie.decision == "CONFLICT":
            heading = "⚠️ OTT AI Research Conflict"
        elif movie.decision == "NEEDS_REVIEW":
            heading = "⚠️ OTT AI Research Needs Review"
        elif movie.database_changed:
            heading = "✅ OTT AI Database Update"
        else:
            heading = "🤖 OTT AI Research"
        return "\n".join((
            heading,
            f"Movie: {movie.title} ({movie.release_year or 'year unknown'})",
            f"TMDB: {movie.tmdb_id or 'Unknown'}",
            f"Run: {movie.run_id[:12]}",
            f"Status: {movie.status}",
            f"Queries: {len(movie.queries)}",
            f"Sources discovered: {movie.sources_discovered}",
            f"Sources inspected: {movie.sources_inspected}",
            f"Model: {movie.model or 'Unknown'}",
            f"Duration: {runtime}",
            f"Platform: {platforms}",
            f"OTT Date: {dates}",
            "Evidence:",
            *evidence,
            f"Platform confidence: {movie.platform_confidence:.0f}%",
            f"Date confidence: {movie.date_confidence:.0f}%",
            f"Decision: {movie.decision or 'UNKNOWN'}",
            f"Mode: {'DRY RUN' if movie.dry_run else 'LIVE'}",
            f"Database changed: {'YES' if movie.database_changed else 'NO'}",
            f"Admin: {settings.SITE_URL.rstrip('/')}/admin/ai-activity/movies/{movie.id}",
        ))


class AIResearchAuditService:
    def __init__(self, db: Session, notifier: AIResearchDiscordNotifier | None = None):
        self.db = db
        self.notifier = notifier or AIResearchDiscordNotifier()

    def create_run(
        self,
        *,
        run_id: str | None = None,
        trigger: str = "MANUAL",
        initiated_by: str | None = None,
        mode: str = "DRY_RUN",
        provider: str | None = None,
        model: str | None = None,
        movies_total: int = 1,
    ) -> AIResearchAuditRun:
        run_id = run_id or str(uuid4())
        existing = self.db.get(AIResearchAuditRun, run_id)
        if existing:
            return existing
        run = AIResearchAuditRun(
            run_id=run_id,
            started_at=utcnow(),
            status="RUNNING",
            trigger=str(trigger).upper()[:32],
            initiated_by=sanitize_error(initiated_by, limit=120) if initiated_by else None,
            mode=str(mode).upper()[:24],
            provider=(provider or settings.AI_RESEARCH_PROVIDER)[:100] or None,
            model=(model or settings.AI_RESEARCH_MODEL)[:200] or None,
            movies_total=max(0, movies_total),
        )
        self.db.add(run)
        self.db.commit()
        self.event(run.run_id, "RUN_STARTED", "AI research run started", metadata={"movies_total": run.movies_total, "mode": run.mode})
        self._notify(run.run_id, None, self.notifier.run_started(run))
        return run

    def start_movie(self, run_id: str, identity: Any, *, dry_run: bool, provider: str, model: str) -> AIResearchMovieActivity:
        existing = self.db.query(AIResearchMovieActivity).filter_by(
            run_id=run_id, movie_id=identity.local_movie_id
        ).one_or_none()
        if existing:
            return existing
        movie = AIResearchMovieActivity(
            run_id=run_id,
            movie_id=identity.local_movie_id,
            tmdb_id=identity.tmdb_id,
            imdb_id=identity.imdb_id,
            title=identity.title[:500],
            original_title=(identity.original_title or "")[:500] or None,
            release_year=identity.release_year,
            language=identity.original_language,
            production_countries=list(identity.production_countries),
            started_at=utcnow(),
            status="RUNNING",
            provider=provider[:100] or None,
            model=model[:200] or None,
            dry_run=dry_run,
        )
        self.db.add(movie)
        self.db.commit()
        self.event(run_id, "MOVIE_STARTED", f"Research started for {movie.title}", movie_activity_id=movie.id, movie_id=movie.movie_id)
        return movie

    def event(
        self,
        run_id: str,
        event_type: str,
        message: str | None = None,
        *,
        movie_activity_id: int | None = None,
        movie_id: int | None = None,
        metadata: dict | None = None,
    ) -> AIResearchEvent:
        query = self.db.query(func.max(AIResearchEvent.sequence)).filter(AIResearchEvent.run_id == run_id)
        if movie_activity_id is None:
            query = query.filter(AIResearchEvent.movie_activity_id.is_(None))
        else:
            query = query.filter(AIResearchEvent.movie_activity_id == movie_activity_id)
        sequence = int(query.scalar() or 0) + 1
        row = AIResearchEvent(
            run_id=run_id,
            movie_activity_id=movie_activity_id,
            movie_id=movie_id,
            sequence=sequence,
            occurred_at=utcnow(),
            event_type=str(event_type).upper()[:80],
            message=sanitize_error(message, limit=1000) if message else None,
            metadata_json=safe_value(metadata) if metadata else None,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def set_queries(self, activity_id: int, queries: list[str]) -> None:
        movie = self.db.get(AIResearchMovieActivity, activity_id)
        movie.queries = [sanitize_error(query, limit=1000) for query in queries[:20]]
        self.db.commit()
        for index, query in enumerate(movie.queries, start=1):
            self.event(movie.run_id, "QUERY_CREATED", f"Research query {index} created", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"query": query})

    def provider_event(self, run_id: str, activity_id: int, movie_id: int, item: dict) -> None:
        event_type = str(item.get("type") or "PROVIDER_ERROR").upper()
        if event_type == "PROVIDER_FALLBACK":
            run = self.db.get(AIResearchAuditRun, run_id)
            run.fallback_count += 1
            self.db.commit()
            message = f"Provider fallback: {item.get('from_provider') or 'unknown'} → {item.get('to_provider') or 'unknown'}"
        else:
            message = f"Provider attempt failed: {item.get('error_type') or 'unknown error'}"
        self.event(
            run_id,
            event_type,
            message,
            movie_activity_id=activity_id,
            movie_id=movie_id,
            metadata=item,
        )

    def record_source(self, activity_id: int, finding: Any) -> AIResearchSource:
        movie = self.db.get(AIResearchMovieActivity, activity_id)
        url = safe_source_url(finding.source_url)
        rejection = list(dict.fromkeys((finding.rejection_reasons or []) + (finding.date_rejection_reasons or [])))
        accepted = bool(finding.inspected and finding.platform and not finding.rejection_reasons)
        now = utcnow()
        row = AIResearchSource(
            run_id=movie.run_id,
            movie_activity_id=movie.id,
            movie_id=movie.movie_id,
            url=url,
            domain=(finding.source_domain or urlsplit(url).hostname or "unknown")[:255],
            page_title=sanitize_error(finding.source_title, limit=500) if finding.source_title else None,
            source_tier=(finding.source_type or "unknown")[:80],
            discovered_at=now,
            fetched_at=now if finding.inspected else None,
            fetch_status="FETCHED" if finding.inspected else "REJECTED",
            identity_match="MATCHED" if finding.inspected and finding.identity_confidence >= 85 else "REJECTED",
            platform_evidence=(finding.platform or "")[:300] or None,
            date_evidence=finding.release_date,
            region_evidence=(finding.region or "")[:32] or None,
            watch_type_evidence=(finding.watch_type or "")[:64] or None,
            accepted=accepted,
            rejection_reason=", ".join(rejection)[:1000] or None,
            evidence_excerpt=sanitize_error(finding.evidence_summary, limit=1000) if finding.evidence_summary else None,
        )
        self.db.add(row)
        self.db.commit()
        self.event(movie.run_id, "SOURCE_DISCOVERED", f"Source discovered: {row.domain}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"source_id": row.id, "url": row.url, "tier": row.source_tier})
        self.event(movie.run_id, "SOURCE_FETCH_STARTED", f"Source inspection started: {row.domain}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"source_id": row.id})
        self.event(movie.run_id, "SOURCE_FETCH_COMPLETED" if finding.inspected else "SOURCE_FETCH_FAILED", f"Source {'inspected' if finding.inspected else 'could not be validated'}: {row.domain}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"source_id": row.id, "status": row.fetch_status})
        self.event(movie.run_id, "IDENTITY_CHECK", f"Identity {row.identity_match.lower()} for {row.domain}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"confidence": finding.identity_confidence})
        if finding.platform:
            self.event(movie.run_id, "PLATFORM_EXTRACTED", f"Platform extracted: {finding.platform}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"confidence": finding.platform_confidence})
        if finding.release_date:
            self.event(movie.run_id, "DATE_EXTRACTED", f"OTT date extracted: {finding.release_date.isoformat()}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"confidence": finding.date_confidence})
        self.event(movie.run_id, "REGION_CLASSIFIED", f"Region classified: {finding.region}", movie_activity_id=movie.id, movie_id=movie.movie_id)
        self.event(movie.run_id, "WATCH_TYPE_CLASSIFIED", f"Watch type classified: {finding.watch_type}", movie_activity_id=movie.id, movie_id=movie.movie_id)
        self.event(movie.run_id, "CONFIDENCE_CALCULATED", "Evidence confidence calculated", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"identity": finding.identity_confidence, "platform": finding.platform_confidence, "date": finding.date_confidence})
        self.event(movie.run_id, "SOURCE_ACCEPTED" if accepted else "SOURCE_REJECTED", f"Source {'accepted' if accepted else 'rejected'}: {row.domain}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"source_id": row.id, "reasons": rejection})
        return row

    def complete_movie(
        self,
        activity_id: int,
        report: Any,
        *,
        before_value: dict | None,
        proposed_value: dict | None,
        after_value: dict | None,
        database_changed: bool,
    ) -> AIResearchMovieActivity:
        movie = self.db.get(AIResearchMovieActivity, activity_id)
        completed = utcnow()
        accepted = [item for item in report.findings if item.inspected and item.platform and not item.rejection_reasons]
        movie.completed_at = completed
        movie.duration_seconds = _seconds(movie.started_at, completed)
        movie.status = "FAILED" if report.provider_error else "COMPLETED"
        movie.provider = (report.provider or movie.provider or "")[:100] or None
        movie.model = (report.model or movie.model or "")[:200] or None
        movie.queries = [sanitize_error(item, limit=1000) for item in report.queries[:20]]
        movie.sources_discovered = self.db.query(AIResearchSource).filter_by(movie_activity_id=movie.id).count()
        movie.sources_inspected = self.db.query(AIResearchSource).filter_by(movie_activity_id=movie.id, fetch_status="FETCHED").count()
        movie.platforms_found = sorted({item.platform for item in accepted if item.platform})
        movie.dates_found = sorted({item.release_date.isoformat() for item in accepted if item.release_date and not item.date_rejection_reasons})
        movie.regions_found = sorted({item.region for item in accepted if item.region})
        movie.watch_types_found = sorted({item.watch_type for item in accepted if item.watch_type})
        movie.identity_confidence = max((item.identity_confidence for item in report.findings), default=0)
        movie.platform_confidence = max((item.platform_confidence for item in accepted), default=0)
        movie.date_confidence = max((item.date_confidence for item in accepted if item.release_date), default=0)
        movie.decision = str(getattr(report.decision, "value", report.decision))[:64]
        movie.error_type = (report.provider_error or "")[:80] or None
        movie.error_summary = sanitize_error(report.provider_error, limit=1000) if report.provider_error else None
        movie.database_changed = bool(database_changed)
        movie.before_value = safe_value(before_value)
        movie.proposed_value = safe_value(proposed_value)
        movie.after_value = safe_value(after_value)
        self.db.commit()
        self.event(movie.run_id, "DECISION_MADE", f"Decision: {movie.decision}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"decision": movie.decision, "provider_error": movie.error_type})
        if movie.database_changed:
            self.event(movie.run_id, "DATABASE_WRITE_PERFORMED", "Verified AI evidence changed database state", movie_activity_id=movie.id, movie_id=movie.movie_id)
        else:
            self.event(movie.run_id, "DATABASE_WRITE_SKIPPED", "Database write skipped — dry run or no accepted change", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"dry_run": movie.dry_run})
        if movie.decision == "NEEDS_REVIEW":
            self.event(movie.run_id, "NEEDS_REVIEW", "Research requires administrator review", movie_activity_id=movie.id, movie_id=movie.movie_id)
        elif movie.decision == "CONFLICT":
            self.event(movie.run_id, "CONFLICT", "Research conflicts with protected or corroborated data", movie_activity_id=movie.id, movie_id=movie.movie_id)
        if movie.error_type:
            error_event = {
                "PROVIDER_QUOTA_EXCEEDED": "PROVIDER_QUOTA_EXCEEDED",
                "PROVIDER_RATE_LIMITED": "PROVIDER_RATE_LIMIT",
            }.get(movie.error_type, "PROVIDER_ERROR")
            self.event(movie.run_id, error_event, movie.error_summary, movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"error_type": movie.error_type})
        self.event(movie.run_id, "MOVIE_COMPLETED", f"Research completed for {movie.title}", movie_activity_id=movie.id, movie_id=movie.movie_id, metadata={"status": movie.status, "decision": movie.decision})
        self.recalculate_run(movie.run_id)
        sources = self.db.query(AIResearchSource).filter_by(movie_activity_id=movie.id, accepted=True).order_by(AIResearchSource.id).limit(2).all()
        if movie.sources_discovered or movie.error_type or movie.decision in {"NEEDS_REVIEW", "CONFLICT", "AUTO_ACCEPT"}:
            self._notify(movie.run_id, movie.id, self.notifier.movie_completed(movie, sources))
        return movie

    def recalculate_run(self, run_id: str) -> AIResearchAuditRun:
        run = self.db.get(AIResearchAuditRun, run_id)
        movies = self.db.query(AIResearchMovieActivity).filter_by(run_id=run_id).all()
        completed = [item for item in movies if item.status in {"COMPLETED", "FAILED"}]
        run.movies_processed = len(completed)
        run.successful = sum(item.decision == "AUTO_ACCEPT" for item in completed)
        run.needs_review = sum(item.decision == "NEEDS_REVIEW" for item in completed)
        run.conflicts = sum(item.decision == "CONFLICT" for item in completed)
        run.no_reliable_result = sum(item.decision == "NO_RELIABLE_RESULT" for item in completed)
        run.provider_failures = sum(bool(item.error_type) for item in completed)
        run.sources_discovered = sum(item.sources_discovered for item in completed)
        run.sources_inspected = sum(item.sources_inspected for item in completed)
        run.database_updates = sum(item.database_changed for item in completed)
        self.db.commit()
        return run

    def complete_run(self, run_id: str, *, status: str = "COMPLETED", error: object | None = None) -> AIResearchAuditRun:
        run = self.recalculate_run(run_id)
        completed = utcnow()
        run.completed_at = completed
        run.duration_seconds = _seconds(run.started_at, completed)
        run.status = status[:32]
        run.error_summary = sanitize_error(error, limit=1000) if error else None
        self.db.commit()
        self.event(run_id, "RUN_COMPLETED" if status == "COMPLETED" else "RUN_FAILED", "AI research run completed" if status == "COMPLETED" else run.error_summary, metadata={"movies_processed": run.movies_processed, "provider_failures": run.provider_failures})
        self._notify(run_id, None, self.notifier.run_completed(run))
        return run

    def _notify(self, run_id: str, activity_id: int | None, content: str) -> None:
        if not self.notifier.configured:
            return
        try:
            self.notifier.send(content)
        except Exception as exc:
            movie_id = None
            if activity_id:
                movie = self.db.get(AIResearchMovieActivity, activity_id)
                movie_id = movie.movie_id if movie else None
            self.event(
                run_id,
                "DISCORD_NOTIFICATION_FAILED",
                sanitize_error(exc, limit=1000),
                movie_activity_id=activity_id,
                movie_id=movie_id,
                metadata={"notification": "discord"},
            )


def compact_json(value: Any) -> str:
    return json.dumps(safe_value(value), ensure_ascii=True, separators=(",", ":"))
