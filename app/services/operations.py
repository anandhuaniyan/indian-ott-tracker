"""Safe, batched operational services used by workers and admin APIs."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_
from sqlalchemy.orm import Session
from app.models.movie import Movie
from app.models.movie_metadata import MovieCredit
from app.models.operations import DataQualityIssue, OttEvidence, OperationState
from app.models.ott_availability import OttAvailability

OPEN = {"UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "CONFLICTING", "NOT_FOUND", "NEEDS_REVIEW", "FAILED"}

class DataHealthService:
    def __init__(self, db: Session): self.db = db
    def _issue(self, movie_id: int, issue_type: str, severity="warning", detail=None):
        item = self.db.query(DataQualityIssue).filter_by(movie_id=movie_id, issue_type=issue_type, resolved_at=None).first()
        if not item:
            self.db.add(DataQualityIssue(movie_id=movie_id, issue_type=issue_type, severity=severity, detail=detail))
    def _resolve(self, movie_id: int, issue_type: str):
        self.db.query(DataQualityIssue).filter_by(movie_id=movie_id, issue_type=issue_type, resolved_at=None).update({"resolved_at": datetime.now(timezone.utc)})
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
            checks["missing_ott"] = not movie.ott_availabilities
            for issue, broken in checks.items():
                if broken: self._issue(movie.id, issue); counts["created_or_open"] += 1
                else: self._resolve(movie.id, issue)
        state.cursor = movies[-1].id; state.last_success_at = datetime.now(timezone.utc)
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
    def record_evidence(self, movie_id: int, *, platform=None, release_date=None, source_url=None, source_title=None, confidence=0, summary=None, source_rank="unknown"):
        now = datetime.now(timezone.utc)
        credible = self.db.query(OttEvidence).filter(OttEvidence.movie_id == movie_id, OttEvidence.status == "CONFIRMED").all()
        conflict = any(x.platform != platform or x.release_date != release_date for x in credible if x.platform or x.release_date)
        status = "CONFLICTING" if conflict else ("CONFIRMED" if confidence >= self.threshold else "POSSIBLE")
        evidence = OttEvidence(movie_id=movie_id, status=status, platform=platform, release_date=release_date, source_url=source_url, source_title=source_title, confidence=confidence, summary=summary, discovered_at=now, last_checked=now, next_check=now + timedelta(days=1 if status == "CONFIRMED" else 7), notes=f"source_rank={source_rank}")
        self.db.add(evidence)
        if conflict:
            DataHealthService(self.db)._issue(movie_id, "ott_conflicting", "high", "Credible OTT sources disagree")
        if status == "CONFIRMED" and platform:
            canonical = self.db.query(OttAvailability).filter_by(movie_id=movie_id, provider=platform, country="IN", watch_type="subscription").first()
            if not canonical:
                canonical = OttAvailability(movie_id=movie_id, provider=platform, country="IN", watch_type="subscription")
                self.db.add(canonical)
            # Confirmed data may only replace absent or lower-confidence canonical information.
            if canonical.confidence <= confidence:
                canonical.ott_release_date, canonical.source_url, canonical.confidence = release_date, source_url, confidence
                canonical.source_type, canonical.status, canonical.last_checked = "RESEARCH", "confirmed", now
        self.db.commit(); return evidence
