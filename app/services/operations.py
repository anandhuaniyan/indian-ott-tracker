"""Safe, batched operational services used by workers and admin APIs."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieImage, MovieReleaseDate
from app.models.operations import DataQualityIssue, OttEvidence, OperationState
from app.models.ott_availability import OttAvailability
from app.services.release_status import ReleaseStatusService, confirmed_canonical_ott
from app.services.roles import ROLE_ALIASES

OPEN = {"UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "CONFLICTING", "NOT_FOUND", "NEEDS_REVIEW", "FAILED"}

class DataHealthService:
    def __init__(self, db: Session): self.db = db
    def _issue(self, movie_id: int | None, issue_type: str, severity="warning", detail=None, person_id=None):
        item = self.db.query(DataQualityIssue).filter_by(movie_id=movie_id, person_id=person_id, issue_type=issue_type, resolved_at=None).first()
        if not item:
            self.db.add(DataQualityIssue(movie_id=movie_id, person_id=person_id, issue_type=issue_type, severity=severity, detail=detail))
        else:
            item.severity, item.detail = severity, detail
    def _resolve(self, movie_id: int | None, issue_type: str, person_id=None):
        self.db.query(DataQualityIssue).filter_by(movie_id=movie_id, person_id=person_id, issue_type=issue_type, resolved_at=None).update({"resolved_at": datetime.now(timezone.utc)})
    def _set(self, movie_id, checks):
        for issue, value in checks.items():
            broken, severity, detail = value if isinstance(value, tuple) else (value, "warning", None)
            if broken: self._issue(movie_id, issue, severity, detail)
            else: self._resolve(movie_id, issue)
    def scan(self, batch_size=250):
        """Scan a bounded batch; repeated invocations eventually cover all rows."""
        state = self.db.query(OperationState).filter_by(name="data_health").first()
        if not state: state = OperationState(name="data_health"); self.db.add(state); self.db.flush()
        movies = self.db.query(Movie).filter(Movie.id > state.cursor).order_by(Movie.id).limit(batch_size).all()
        if not movies:
            state.cursor = 0; self.db.commit()
            return {"scanned": 0, "created_or_open": 0, "cycle_complete": True}
        counts = {"scanned": len(movies), "created_or_open": 0}
        release_service = ReleaseStatusService(self.db)
        for movie in movies:
            _, eligibility, latest_evidence = release_service.classify_movie(movie)
            checks = {"missing_poster": not movie.poster_path, "missing_backdrop": not movie.backdrop_path, "missing_title": not movie.title, "missing_release_date": not movie.release_date, "missing_language": not movie.original_language, "missing_genre": not movie.genres}
            credits = self.db.query(MovieCredit).filter_by(movie_id=movie.id).all()
            checks["missing_cast"] = not any(c.credit_type == "cast" for c in credits)
            checks["missing_director"] = not any((c.job or "").lower() == "director" for c in credits)
            checks["missing_cinematographer"] = not any((c.job or "").lower() in ROLE_ALIASES["cinematography"] for c in credits)
            external_ids = self.db.query(ExternalId).filter_by(movie_id=movie.id).all()
            checks["missing_imdb"] = not any(x.provider.lower() in {"imdb", "imdb_id"} for x in external_ids)
            checks["missing_external_ids"] = not external_ids
            releases = self.db.query(MovieReleaseDate).filter_by(movie_id=movie.id).all()
            checks["missing_certification"] = not any(x.certification for x in releases)
            checks["invalid_dates"] = bool(movie.release_date and (movie.release_date.year < 1888 or movie.release_date > datetime.now(timezone.utc).date() + timedelta(days=3650)))
            checks["missing_logo"] = not self.db.query(MovieImage.id).filter_by(movie_id=movie.id, image_type="logo").first()
            canonical = confirmed_canonical_ott(movie)
            checks["missing_ott_provider"] = (
                eligibility.code == "ELIGIBLE" and not movie.ott_availabilities
            )
            checks["missing_ott_release_date"] = (
                eligibility.code == "ELIGIBLE"
                and bool(movie.ott_availabilities)
                and not any(x.ott_release_date for x in movie.ott_availabilities)
            )
            checks["ott_conflicts"] = self.db.query(OttEvidence.id).filter_by(movie_id=movie.id, status="CONFLICTING").first() is not None
            checks["ott_research_failures"] = bool(
                latest_evidence
                and latest_evidence.status == "FAILED"
                and eligibility.code not in {"WAITING_RELEASE", "MIN_DELAY", "METADATA_REPAIR"}
            )
            lifecycle = {
                "ott_not_yet_expected": eligibility.code in {"WAITING_RELEASE", "MIN_DELAY"},
                "ott_metadata_repair": eligibility.code == "METADATA_REPAIR",
                "ott_research_pending": eligibility.code == "ELIGIBLE",
                "ott_researching": bool(latest_evidence and latest_evidence.status == "RESEARCHING"),
                "ott_confirmed": canonical is not None,
                "ott_not_found": bool(latest_evidence and latest_evidence.status == "NOT_FOUND"),
                "ott_conflicting": bool(latest_evidence and latest_evidence.status == "CONFLICTING"),
            }
            for issue_type, active in lifecycle.items():
                if active:
                    severity = "high" if issue_type == "ott_conflicting" else "info"
                    self._issue(movie.id, issue_type, severity, eligibility.label)
                else:
                    self._resolve(movie.id, issue_type)
            checks["duplicate_candidate"] = self.db.query(Movie.id).filter(Movie.id != movie.id, func.lower(Movie.title) == (movie.title or "").lower(), Movie.release_date == movie.release_date).first() is not None
            for issue, broken in checks.items():
                if broken: self._issue(movie.id, issue, "high" if issue in {"missing_title", "invalid_dates", "ott_conflicts"} else "warning"); counts["created_or_open"] += 1
                else: self._resolve(movie.id, issue)
        state.cursor = movies[-1].id; state.processed_count += len(movies); state.last_success_at = datetime.now(timezone.utc); state.last_error = None
        self.db.commit(); return counts | {"cursor": state.cursor, "cycle_complete": False}


class ResearchUsageService:
    """Persistent free-tier guards for Tavily requests and daily movie volume."""

    def __init__(self, db: Session):
        self.db = db

    def _state(self, name: str, limit: int) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=name).with_for_update().first()
        if not state:
            state = OperationState(name=name, total_count=limit, status="ACTIVE")
            self.db.add(state)
            self.db.flush()
        elif state.total_count != limit:
            state.total_count = limit
        return state

    def monthly_snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        state = self._state(
            f"tavily_usage:{now:%Y-%m}", settings.TAVILY_MONTHLY_APP_BUDGET
        )
        self.db.commit()
        return {
            "used": state.processed_count,
            "limit": state.total_count,
            "remaining": max(0, state.total_count - state.processed_count),
        }

    def reserve_tavily_query(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        state = self._state(
            f"tavily_usage:{now:%Y-%m}", settings.TAVILY_MONTHLY_APP_BUDGET
        )
        if state.processed_count >= state.total_count:
            state.status = "EXHAUSTED"
            self.db.commit()
            return False
        state.processed_count += 1
        state.status = "EXHAUSTED" if state.processed_count >= state.total_count else "ACTIVE"
        state.last_success_at = now
        self.db.commit()
        return True

    def daily_snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        state = self._state(
            f"ott_research_daily:{now.date().isoformat()}",
            settings.OTT_DAILY_RESEARCH_MOVIE_LIMIT,
        )
        self.db.commit()
        return {
            "used": state.processed_count,
            "limit": state.total_count,
            "remaining": max(0, state.total_count - state.processed_count),
        }

    def reserve_daily_movie(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        state = self._state(
            f"ott_research_daily:{now.date().isoformat()}",
            settings.OTT_DAILY_RESEARCH_MOVIE_LIMIT,
        )
        if state.processed_count >= state.total_count:
            state.status = "EXHAUSTED"
            self.db.commit()
            return False
        state.processed_count += 1
        state.status = "EXHAUSTED" if state.processed_count >= state.total_count else "ACTIVE"
        state.last_success_at = now
        self.db.commit()
        return True

class OttResearchService:
    """Queue/evidence policy. Retrieval is delegated to configured lawful providers."""
    def __init__(self, db: Session, confirmation_threshold=85.0): self.db, self.threshold = db, confirmation_threshold
    def queue_missing(self, batch_size=100):
        # Classification is local and cheap; it must happen before any queue
        # selection so an unclassified catalogue can never become a Tavily queue.
        ReleaseStatusService(self.db).classify_batch(max(batch_size, 1000))
        movies = self.db.query(Movie).outerjoin(OttAvailability).filter(
            Movie.ott_research_eligibility == "ELIGIBLE"
        ).group_by(Movie.id).having(
            (func.count(OttAvailability.id) == 0) | (func.count(OttAvailability.ott_release_date) < func.count(OttAvailability.id))
        ).order_by(Movie.theatrical_release_date.desc(), Movie.popularity.desc()).limit(batch_size).all()
        now = datetime.now(timezone.utc); added = 0
        for movie in movies:
            added += int(self.queue_movie(movie.id, now=now))
        self.db.commit(); return added
    def queue_movie(self, movie_id: int, *, now=None):
        now = now or datetime.now(timezone.utc)
        movie = self.db.get(Movie, movie_id)
        if not movie:
            return False
        _, eligibility, latest = ReleaseStatusService(self.db).classify_movie(movie, now=now)
        if eligibility.code != "ELIGIBLE":
            return False
        active = self.db.query(OttEvidence).filter(OttEvidence.movie_id == movie_id, OttEvidence.status.in_(OPEN)).first()
        if active:
            return False
        if latest and latest.status in {"WAITING_RELEASE", "METADATA_REPAIR", "TOO_OLD"}:
            previous = latest.status
            latest.status = "QUEUED"
            latest.next_check = now
            latest.notes = f"eligibility=ELIGIBLE; previous_status={previous}"
            return True
        self.db.add(OttEvidence(movie_id=movie_id, status="QUEUED", next_check=now))
        return True
    @staticmethod
    def next_check_for(status: str, release_date=None, attempts=0):
        now = datetime.now(timezone.utc)
        days = {
            "UNKNOWN": 1, "POSSIBLE": 3, "NOT_FOUND": min(90, 7 * 2 ** min(attempts, 3)),
            "CONFLICTING": 1, "FAILED": min(30, 2 ** min(attempts, 5)),
            "NEEDS_REVIEW": 7, "QUEUED": 0, "RESEARCHING": 1,
        }.get(status, 30)
        if status == "CONFIRMED":
            days = 3 if release_date and release_date >= now.date() else 30
        return now + timedelta(days=days)
    def record_evidence(self, movie_id: int, *, platform=None, release_date=None, source_url=None, source_title=None, source_published_at=None, confidence=0, summary=None, source_rank="unknown"):
        now = datetime.now(timezone.utc)
        credible = self.db.query(OttEvidence).filter(OttEvidence.movie_id == movie_id, OttEvidence.status == "CONFIRMED").all()
        conflict = confidence >= self.threshold and source_rank != "unknown" and any(x.platform != platform or x.release_date != release_date for x in credible if x.platform or x.release_date)
        status = "CONFLICTING" if conflict else ("CONFIRMED" if confidence >= self.threshold else "POSSIBLE")
        evidence = OttEvidence(movie_id=movie_id, status=status, platform=platform, release_date=release_date, source_url=source_url, source_title=source_title, source_published_at=source_published_at, confidence=confidence, summary=summary, discovered_at=now, last_checked=now, next_check=self.next_check_for(status, release_date), notes=f"source_rank={source_rank}")
        self.db.add(evidence)
        if conflict:
            DataHealthService(self.db)._issue(movie_id, "ott_conflicting", "high", "Credible OTT sources disagree")
            from app.services.notification_service import NotificationService
            NotificationService(self.db).notify(f"OTT conflict requires review for movie {movie_id}", "high", f"ott-conflict:{movie_id}")
        if status == "CONFIRMED" and platform:
            canonical = self.db.query(OttAvailability).filter_by(movie_id=movie_id, provider=platform, country="IN", watch_type="subscription").first()
            if not canonical:
                canonical = OttAvailability(movie_id=movie_id, provider=platform, country="IN", watch_type="subscription")
                self.db.add(canonical)
            # Confirmed data may only replace absent or lower-confidence canonical information.
            if (canonical.confidence or 0) <= confidence:
                canonical.ott_release_date, canonical.source_url, canonical.confidence = release_date, source_url, confidence
                canonical.source_type, canonical.status, canonical.last_checked = "RESEARCH", "confirmed", now
        if status == "CONFIRMED": DataHealthService(self.db)._resolve(movie_id, "ott_conflicting")
        self.db.commit(); return evidence
