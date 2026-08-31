"""Opt-in OTTplay/JustWatch adapter ingestion with durable matching statistics.

The project does not scrape either service.  Operators may configure a lawful
JSON adapter/feed they are entitled to use; disabled adapters remain isolated
from the canonical OTT research pipeline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.operations import OperationState, OttEvidence, OttSourceRelease
from app.services.operations import OttResearchService


SOURCES = {"ottplay", "justwatch"}


def _normalized_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _safe_source_url(value) -> str | None:
    value = str(value or "").strip()
    parsed = urlparse(value)
    return value[:1000] if parsed.scheme == "https" and parsed.hostname else None


class OttSourceSyncService:
    """Synchronize one configured provider adapter without parallel duplicates."""

    def __init__(self, db: Session, source: str):
        source = source.lower()
        if source not in SOURCES:
            raise ValueError("Unsupported OTT source")
        self.db = db
        self.source = source

    @property
    def enabled(self) -> bool:
        return bool(
            settings.OTTPLAY_ENABLED
            if self.source == "ottplay"
            else settings.JUSTWATCH_ENABLED
        )

    @property
    def adapter_url(self) -> str:
        return (
            settings.OTTPLAY_ADAPTER_URL
            if self.source == "ottplay"
            else settings.JUSTWATCH_ADAPTER_URL
        )

    @property
    def api_key(self) -> str:
        return (
            settings.OTTPLAY_API_KEY
            if self.source == "ottplay"
            else settings.JUSTWATCH_API_KEY
        )

    def _state(self, lock=False) -> OperationState:
        query = self.db.query(OperationState).filter_by(name=f"source.{self.source}")
        if lock:
            query = query.with_for_update()
        state = query.first()
        if not state:
            state = OperationState(name=f"source.{self.source}", details={})
            self.db.add(state)
            self.db.flush()
        return state

    def snapshot(self) -> dict:
        state = self._state()
        details = state.details or {}
        return {
            "source": self.source,
            "enabled": self.enabled,
            "configured": bool(self.enabled and self.adapter_url),
            "integration_mode": "Adapter",
            "country": "India",
            "status": state.status if self.enabled else "DISABLED",
            "last_check": state.updated_at,
            "last_success": state.last_success_at,
            "last_failure": state.last_failure_at,
            "last_error": state.last_error,
            "next_run": details.get("next_run"),
            "stats": details.get("stats", {}),
        }

    def sync(self) -> dict:
        now = datetime.now(timezone.utc)
        state = self._state(lock=True)
        if not self.enabled or not self.adapter_url:
            state.status = "DISABLED" if not self.enabled else "BLOCKED"
            state.last_error = None if not self.enabled else "Adapter URL is not configured"
            state.details = {
                **(state.details or {}),
                "next_run": (now + timedelta(days=1)).isoformat(),
                "stats": (state.details or {}).get("stats", {}),
            }
            self.db.commit()
            return self.snapshot()
        updated = state.updated_at
        if updated and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if state.status in {"QUEUED", "RUNNING"} and updated and updated > now - timedelta(minutes=30):
            return {"queued": False, "detail": "Source sync is already running"} | self.snapshot()
        state.status = "RUNNING"
        state.last_error = None
        self.db.commit()
        try:
            headers = {"Accept": "application/json", "User-Agent": "IndianOTTTracker/1.0"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            response = httpx.get(self.adapter_url, headers=headers, timeout=30, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
            items = payload if isinstance(payload, list) else payload.get("items", payload.get("results", []))
            if not isinstance(items, list):
                raise ValueError("Adapter response must contain an items list")
            stats = {
                "pages_checked": int(payload.get("pages_checked", 1)) if isinstance(payload, dict) else 1,
                "movies_discovered": 0,
                "movies_matched": 0,
                "movies_unmatched": 0,
                "new_ott_dates_found": 0,
                "new_platforms_found": 0,
                "conflicts_found": 0,
                "errors": 0,
            }
            for raw in items[: settings.OTT_SOURCE_SYNC_BATCH_SIZE]:
                if not isinstance(raw, dict):
                    stats["errors"] += 1
                    continue
                try:
                    self._ingest(raw, stats, now)
                except Exception:
                    stats["errors"] += 1
            state = self._state()
            state.status = "COMPLETE"
            state.processed_count += stats["movies_discovered"]
            state.last_success_at = now
            state.last_error = None
            state.details = {
                "next_run": (now + timedelta(days=1)).isoformat(),
                "stats": stats,
            }
            self.db.commit()
            return {"queued": False, "processed": stats["movies_discovered"]} | self.snapshot()
        except Exception as exc:
            self.db.rollback()
            state = self._state()
            state.status = "FAILED"
            state.last_failure_at = now
            state.last_error = sanitize_error(exc)
            state.details = {
                **(state.details or {}),
                "next_run": (now + timedelta(hours=6)).isoformat(),
            }
            self.db.commit()
            return {"queued": False, "failed": True} | self.snapshot()

    def _find_movie(self, raw: dict, title: str, language: str | None, release_date: date | None):
        tmdb_id = raw.get("tmdb_id") or raw.get("tmdbId")
        if str(tmdb_id or "").isdigit():
            movie = self.db.query(Movie).filter_by(tmdb_id=int(tmdb_id)).first()
            if movie:
                return movie, "TMDB ID"
        imdb_id = str(raw.get("imdb_id") or raw.get("imdbId") or "").strip()
        if imdb_id:
            pair = (
                self.db.query(Movie)
                .join(ExternalId, ExternalId.movie_id == Movie.id)
                .filter(func.lower(ExternalId.provider) == "imdb", ExternalId.external_id == imdb_id)
                .first()
            )
            if pair:
                return pair, "IMDb ID"
        normalized = _normalized_title(title)
        if not normalized:
            return None, None
        raw_title = title.casefold().strip()
        candidates = self.db.query(Movie).filter(
            or_(func.lower(Movie.title) == raw_title, func.lower(Movie.original_title) == raw_title)
        )
        if language:
            candidates = candidates.filter(Movie.original_language == language)
        rows = candidates.limit(3).all()
        if not rows:
            # Provider feeds often vary punctuation (for example, hyphens versus
            # colons). Narrow by one token in SQL, then normalize only the small
            # candidate set in Python so this remains portable across SQLite and
            # PostgreSQL without weakening identifier-first matching.
            token = normalized.split()[0]
            candidates = self.db.query(Movie).filter(
                or_(Movie.title.ilike(f"%{token}%"), Movie.original_title.ilike(f"%{token}%"))
            )
            if language:
                candidates = candidates.filter(Movie.original_language == language)
            rows = [
                movie
                for movie in candidates.limit(100).all()
                if normalized in {_normalized_title(movie.title), _normalized_title(movie.original_title)}
            ][:3]
        if release_date:
            matching_year = [m for m in rows if m.release_date and abs(m.release_date.year - release_date.year) <= 1]
            rows = matching_year or rows
        return (rows[0], "Exact title/language/year") if len(rows) == 1 else (None, None)

    def _ingest(self, raw: dict, stats: dict, now: datetime) -> OttSourceRelease:
        title = str(raw.get("title") or raw.get("movie_title") or "").strip()
        if not title:
            raise ValueError("Source record has no title")
        release_date = _date(raw.get("ott_release_date") or raw.get("release_date") or raw.get("date"))
        language = str(raw.get("language") or raw.get("original_language") or "").strip().lower() or None
        platform = str(raw.get("platform") or raw.get("provider") or "").strip() or None
        source_url = _safe_source_url(raw.get("source_url") or raw.get("url"))
        raw_key = raw.get("id") or raw.get("external_id") or raw.get("source_id")
        stable = "|".join([title.casefold(), str(release_date or ""), platform or "", language or "", source_url or ""])
        external_key = str(raw_key or hashlib.sha256(stable.encode()).hexdigest())[:160]
        record = self.db.query(OttSourceRelease).filter_by(source=self.source, external_key=external_key).first()
        if not record:
            record = OttSourceRelease(source=self.source, external_key=external_key, title=title, first_seen_at=now)
            self.db.add(record)
        record.title = title
        record.original_title = str(raw.get("original_title") or "").strip() or None
        record.platform = platform
        record.release_date = release_date
        record.language = language
        record.source_url = source_url
        record.last_seen_at = now
        movie, reason = self._find_movie(raw, title, language, release_date)
        if movie and record.status not in {"IGNORED", "TV_SERIES", "DUPLICATE"}:
            record.status = "MATCHED"
            record.matched_movie_id = movie.id
            record.match_reason = reason
            stats["movies_matched"] += 1
            existing = self.db.query(OttEvidence.id).filter(
                OttEvidence.movie_id == movie.id,
                OttEvidence.source_type == self.source,
                OttEvidence.source_url == source_url,
                OttEvidence.platform == platform,
                OttEvidence.release_date == release_date,
            ).first()
            if not existing and source_url and platform:
                confidence = 82.0 if self.source == "ottplay" else 75.0
                OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD).record_evidence(
                    movie.id,
                    platform=platform,
                    release_date=release_date,
                    source_url=source_url,
                    source_title=title,
                    confidence=confidence,
                    summary=f"Availability discovered by configured {self.source.title()} adapter",
                    source_type=self.source,
                    source_name=self.source.title(),
                    country="IN",
                    inspected=True,
                )
                if release_date:
                    stats["new_ott_dates_found"] += 1
                stats["new_platforms_found"] += 1
        elif record.status not in {"IGNORED", "TV_SERIES", "DUPLICATE"}:
            record.status = "UNMATCHED"
            record.matched_movie_id = None
            record.match_reason = None
            stats["movies_unmatched"] += 1
        stats["movies_discovered"] += 1
        self.db.flush()
        return record
