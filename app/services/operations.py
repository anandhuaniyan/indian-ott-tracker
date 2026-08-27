"""Safe, batched operational services used by workers and admin APIs."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieImage, MovieReleaseDate
from app.models.operations import DataQualityIssue, OttEvidence, OperationState
from app.models.ott_availability import OttAvailability
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
        for movie in movies:
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
            checks["missing_ott_provider"] = not movie.ott_availabilities
            checks["missing_ott_release_date"] = bool(movie.ott_availabilities) and not any(x.ott_release_date for x in movie.ott_availabilities)
            checks["ott_conflicts"] = self.db.query(OttEvidence.id).filter_by(movie_id=movie.id, status="CONFLICTING").first() is not None
            checks["ott_research_failures"] = self.db.query(OttEvidence.id).filter_by(movie_id=movie.id, status="FAILED").first() is not None
            checks["duplicate_candidate"] = self.db.query(Movie.id).filter(Movie.id != movie.id, func.lower(Movie.title) == (movie.title or "").lower(), Movie.release_date == movie.release_date).first() is not None
            for issue, broken in checks.items():
                if broken: self._issue(movie.id, issue, "high" if issue in {"missing_title", "invalid_dates", "ott_conflicts"} else "warning"); counts["created_or_open"] += 1
                else: self._resolve(movie.id, issue)
        state.cursor = movies[-1].id; state.processed_count += len(movies); state.last_success_at = datetime.now(timezone.utc); state.last_error = None
        self.db.commit(); return counts | {"cursor": state.cursor, "cycle_complete": False}

class OttResearchService:
    """Queue/evidence policy. Retrieval is delegated to configured lawful providers."""
    def __init__(self, db: Session, confirmation_threshold=85.0): self.db, self.threshold = db, confirmation_threshold
    def queue_missing(self, batch_size=100):
        movies = self.db.query(Movie).outerjoin(OttAvailability).filter(OttAvailability.id.is_(None)).limit(batch_size).all()
        now = datetime.now(timezone.utc); added = 0
        for movie in movies:
            active = self.db.query(OttEvidence).filter(OttEvidence.movie_id == movie.id, OttEvidence.status.in_(OPEN)).first()
            if not active:
                self.db.add(OttEvidence(movie_id=movie.id, status="QUEUED", next_check=now)); added += 1
        self.db.commit(); return added
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
    def record_evidence(self, movie_id: int, *, platform=None, release_date=None, source_url=None, source_title=None, confidence=0, summary=None, source_rank="unknown"):
        now = datetime.now(timezone.utc)
        credible = self.db.query(OttEvidence).filter(OttEvidence.movie_id == movie_id, OttEvidence.status == "CONFIRMED").all()
        conflict = confidence >= self.threshold and source_rank != "unknown" and any(x.platform != platform or x.release_date != release_date for x in credible if x.platform or x.release_date)
        status = "CONFLICTING" if conflict else ("CONFIRMED" if confidence >= self.threshold else "POSSIBLE")
        evidence = OttEvidence(movie_id=movie_id, status=status, platform=platform, release_date=release_date, source_url=source_url, source_title=source_title, confidence=confidence, summary=summary, discovered_at=now, last_checked=now, next_check=self.next_check_for(status, release_date), notes=f"source_rank={source_rank}")
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
