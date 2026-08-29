"""Stored-release classification and conservative OTT research eligibility.

No provider calls are made here.  The service classifies movies exclusively
from database release rows and canonical OTT records, making it safe to run
across the complete catalogue as often as dates change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.models.movie import Movie
from app.models.operations import MovieRequest, OperationState, OttEvidence

THEATRICAL_RELEASE_TYPES = {"2", "3", "limited theatrical", "theatrical"}
DIGITAL_RELEASE_TYPES = {"4", "digital", "ott", "streaming"}
CANONICAL_OTT_STATES = {"available", "confirmed", "released", "upcoming"}
INDIAN_ORIGINAL_LANGUAGES = {
    "as", "bn", "gu", "hi", "kn", "ks", "ml", "mr", "ne", "or", "pa",
    "sa", "sd", "ta", "te", "ur",
}

RELEASE_LABELS = {
    "THEATRICALLY_RELEASED": "Released",
    "UPCOMING": "Upcoming",
    "DIRECT_TO_OTT": "Direct-to-OTT",
    "UNKNOWN": "Unknown",
}

ELIGIBILITY_LABELS = {
    "ELIGIBLE": "Eligible",
    "WAITING_RELEASE": "Waiting for theatrical release",
    "MIN_DELAY": "Waiting for OTT research window",
    "METADATA_REPAIR": "Awaiting TMDB release information",
    "CONFIRMED": "Confirmed OTT information available",
    "COOLDOWN": "Research cooldown active",
    "TOO_OLD": "Manual research only",
}

RESEARCH_STATUS_LABELS = {
    "UNKNOWN": "Awaiting review",
    "QUEUED": "Queued",
    "RESEARCHING": "Researching",
    "POSSIBLE": "Possible result under review",
    "CONFIRMED": "Confirmed",
    "CONFLICTING": "Conflicting sources",
    "NOT_FOUND": "Not found",
    "NEEDS_REVIEW": "Needs review",
    "FAILED": "Retry scheduled",
    "WAITING_RELEASE": "Waiting for theatrical release",
    "METADATA_REPAIR": "Awaiting TMDB release information",
    "TOO_OLD": "Manual research only",
}

_UNSET = object()


def _site_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.SITE_TIMEZONE)
    except (KeyError, ValueError):
        return ZoneInfo("Asia/Kolkata")


def site_date(value: datetime | None = None) -> date:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(_site_timezone()).date()


def _site_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=_site_timezone()).astimezone(timezone.utc)


@dataclass(frozen=True)
class ReleaseClassification:
    code: str
    theatrical_date: date | None
    digital_date: date | None

    @property
    def label(self) -> str:
        return RELEASE_LABELS[self.code]


@dataclass(frozen=True)
class ResearchEligibility:
    code: str
    priority: str
    next_eligible_at: datetime | None = None

    @property
    def label(self) -> str:
        return ELIGIBILITY_LABELS[self.code]


def _release_type(value: str | None) -> str:
    return (value or "").strip().lower()


def _preferred_date(movie: Movie, rows) -> date | None:
    """Choose a primary theatrical event without inventing language metadata.

    Release-event rows do not reliably carry a language.  For an Indian
    original-language movie, an India event is therefore the strongest
    defensible domestic signal.  A wide theatrical event is preferred over a
    limited event inside each tier, followed by the earliest reliable date.
    """

    rows = list(rows)
    if not rows:
        return None

    def select(pool) -> date | None:
        pool = list(pool)
        wide = [
            row for row in pool
            if _release_type(row.release_type) in {"3", "theatrical"}
        ]
        candidates = wide or pool
        return min((row.release_date for row in candidates), default=None)

    if (movie.original_language or "").lower() in INDIAN_ORIGINAL_LANGUAGES:
        domestic = [row for row in rows if (row.country or "").upper() == "IN"]
        if domestic:
            return select(domestic)
    indian = [row for row in rows if (row.country or "").upper() == "IN"]
    if indian:
        return select(indian)
    return select(rows)


def confirmed_canonical_ott(movie: Movie):
    """Return the strongest complete canonical OTT row, if one exists."""
    candidates = [
        item
        for item in movie.ott_availabilities
        if item.provider
        and item.ott_release_date
        and item.verification_status == "CONFIRMED"
        and (item.status or "").lower() in CANONICAL_OTT_STATES
        and (item.confidence or 0) >= settings.OTT_CONFIRMATION_THRESHOLD
    ]
    return max(candidates, key=lambda item: ((item.confidence or 0), item.ott_release_date), default=None)


def best_canonical_ott(movie: Movie):
    """Return a public summary row without treating incomplete data as confirmed."""
    candidates = [
        item
        for item in movie.ott_availabilities
        if item.provider and (item.status or "").lower() in CANONICAL_OTT_STATES
    ]
    return max(
        candidates,
        key=lambda item: (
            item.verification_status == "CONFIRMED" and bool(item.ott_release_date),
            (item.confidence or 0),
            item.ott_release_date if item.verification_status == "CONFIRMED" else date.min,
        ),
        default=None,
    )


def classify_release(movie: Movie, *, today: date | None = None) -> ReleaseClassification:
    """Classify from explicit theatrical/digital rows and canonical OTT only."""
    today = today or site_date()
    theatrical = [
        row for row in movie.release_dates if _release_type(row.release_type) in THEATRICAL_RELEASE_TYPES
    ]
    theatrical_date = _preferred_date(movie, theatrical)
    if theatrical_date:
        return ReleaseClassification(
            "THEATRICALLY_RELEASED" if theatrical_date <= today else "UPCOMING",
            theatrical_date,
            None,
        )

    digital = [row for row in movie.release_dates if _release_type(row.release_type) in DIGITAL_RELEASE_TYPES]
    digital_date = _preferred_date(movie, digital)
    canonical = confirmed_canonical_ott(movie)
    if digital_date or canonical:
        return ReleaseClassification(
            "DIRECT_TO_OTT",
            None,
            canonical.ott_release_date if canonical else digital_date,
        )
    # Preserve the last explicit high-confidence classification if release
    # history is temporarily incomplete, then fall back to the provider's
    # general release date. Neither value is ever taken from watch providers.
    fallback_date = movie.theatrical_release_date or movie.release_date
    if fallback_date:
        return ReleaseClassification(
            "THEATRICALLY_RELEASED" if fallback_date <= today else "UPCOMING",
            fallback_date,
            None,
        )
    return ReleaseClassification("UNKNOWN", None, None)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def research_eligibility(
    movie: Movie,
    classification: ReleaseClassification,
    *,
    latest_evidence: OttEvidence | None = None,
    requested: bool = False,
    now: datetime | None = None,
) -> ResearchEligibility:
    now = now or datetime.now(timezone.utc)
    today = site_date(now)
    if confirmed_canonical_ott(movie):
        return ResearchEligibility("CONFIRMED", "NONE")
    if classification.code == "UPCOMING":
        eligible_date = classification.theatrical_date + timedelta(
            days=settings.OTT_RESEARCH_MIN_DAYS_AFTER_THEATRICAL_RELEASE
        )
        return ResearchEligibility(
            "WAITING_RELEASE",
            "NONE",
            _site_midnight(eligible_date),
        )
    if classification.code == "UNKNOWN":
        return ResearchEligibility("METADATA_REPAIR", "NONE", now + timedelta(days=1))

    release_date = classification.theatrical_date or classification.digital_date
    if not release_date:
        return ResearchEligibility("METADATA_REPAIR", "NONE", now + timedelta(days=1))
    age_days = (today - release_date).days
    if age_days < settings.OTT_RESEARCH_MIN_DAYS_AFTER_THEATRICAL_RELEASE:
        eligible_date = release_date + timedelta(
            days=settings.OTT_RESEARCH_MIN_DAYS_AFTER_THEATRICAL_RELEASE
        )
        return ResearchEligibility(
            "MIN_DELAY",
            "NONE",
            _site_midnight(eligible_date),
        )
    if age_days > settings.OTT_RESEARCH_AUTO_MAX_AGE_DAYS and not requested:
        return ResearchEligibility("TOO_OLD", "VERY_LOW")

    next_check = _aware(latest_evidence.next_check) if latest_evidence else None
    if next_check and next_check > now:
        return ResearchEligibility("COOLDOWN", "NONE", next_check)
    if requested:
        priority = "USER_REQUESTED"
    elif age_days <= settings.OTT_RESEARCH_HIGH_PRIORITY_DAYS:
        priority = "HIGH"
    elif age_days <= settings.OTT_RESEARCH_MEDIUM_PRIORITY_DAYS:
        priority = "MEDIUM"
    else:
        priority = "LOW"
    return ResearchEligibility("ELIGIBLE", priority)


class ReleaseStatusService:
    # A versioned cursor applies the improved primary-date policy without
    # resetting or rewriting the completed V1 checkpoint.
    operation = "release_status_classification_v2"

    def __init__(self, db: Session):
        self.db = db

    def latest_evidence(self, movie_id: int) -> OttEvidence | None:
        return (
            self.db.query(OttEvidence)
            .filter(OttEvidence.movie_id == movie_id, OttEvidence.source_url.is_(None))
            .order_by(OttEvidence.updated_at.desc(), OttEvidence.id.desc())
            .first()
        )

    def requested(self, movie: Movie) -> bool:
        query = self.db.query(MovieRequest.id).filter(
            func.lower(MovieRequest.movie_name) == (movie.title or "").lower(),
            MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]),
        )
        if movie.release_date:
            query = query.filter(
                (MovieRequest.release_year.is_(None))
                | (MovieRequest.release_year == movie.release_date.year)
            )
        return query.first() is not None

    def classify_movie(
        self,
        movie: Movie,
        *,
        now: datetime | None = None,
        sync_evidence: bool = True,
        latest_evidence=_UNSET,
        requested: bool | None = None,
    ) -> tuple[ReleaseClassification, ResearchEligibility, OttEvidence | None]:
        now = now or datetime.now(timezone.utc)
        classification = classify_release(movie, today=site_date(now))
        latest = self.latest_evidence(movie.id) if latest_evidence is _UNSET else latest_evidence
        requested = self.requested(movie) if requested is None else requested
        eligibility = research_eligibility(
            movie,
            classification,
            latest_evidence=latest,
            requested=requested,
            now=now,
        )
        movie.release_status_code = classification.code
        movie.theatrical_release_date = classification.theatrical_date
        movie.ott_research_eligibility = eligibility.code
        movie.release_classified_at = now
        if sync_evidence:
            self._sync_ineligible_evidence(latest, eligibility, now)
        return classification, eligibility, latest

    @staticmethod
    def _sync_ineligible_evidence(
        latest: OttEvidence | None,
        eligibility: ResearchEligibility,
        now: datetime,
    ) -> None:
        if not latest or eligibility.code in {"ELIGIBLE", "COOLDOWN"}:
            return
        if latest.status not in {"UNKNOWN", "QUEUED", "RESEARCHING", "POSSIBLE", "NOT_FOUND", "FAILED"}:
            return
        status_for = {
            "WAITING_RELEASE": "WAITING_RELEASE",
            "MIN_DELAY": "WAITING_RELEASE",
            "METADATA_REPAIR": "METADATA_REPAIR",
            "CONFIRMED": "CONFIRMED",
            "TOO_OLD": "TOO_OLD",
        }
        new_status = status_for.get(eligibility.code)
        if not new_status:
            return
        previous = latest.status
        latest.status = new_status
        latest.next_check = eligibility.next_eligible_at
        latest.notes = f"eligibility={eligibility.code}; previous_status={previous}"
        latest.last_checked = latest.last_checked or now

    def classify_batch(self, batch_size: int = 1000, *, restart_completed: bool = False) -> dict:
        batch_size = max(1, min(batch_size, 2000))
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not state:
            state = OperationState(
                name=self.operation,
                status="RUNNING",
                total_count=self.db.query(Movie).count(),
            )
            self.db.add(state)
            self.db.flush()
        elif state.status == "COMPLETE":
            if not restart_completed:
                return {
                    "operation": self.operation,
                    "processed": 0,
                    "complete": True,
                    "counts": self.counts(),
                }
            state.cursor = 0
            state.processed_count = 0
            state.total_count = self.db.query(Movie).count()
            state.status = "RUNNING"
            state.completed_at = None

        movies = (
            self.db.query(Movie)
            .options(selectinload(Movie.release_dates), selectinload(Movie.ott_availabilities))
            .filter(Movie.id > state.cursor)
            .order_by(Movie.id)
            .limit(batch_size)
            .all()
        )
        if not movies:
            state.status = "COMPLETE"
            state.completed_at = datetime.now(timezone.utc)
            state.last_success_at = state.completed_at
            state.last_error = None
            self.db.commit()
            return {
                "operation": self.operation,
                "processed": 0,
                "complete": True,
                "counts": self.counts(),
            }

        now = datetime.now(timezone.utc)
        movie_ids = [movie.id for movie in movies]
        evidence_by_movie = {}
        evidence_rows = (
            self.db.query(OttEvidence)
            .filter(OttEvidence.movie_id.in_(movie_ids), OttEvidence.source_url.is_(None))
            .order_by(OttEvidence.movie_id, OttEvidence.updated_at.desc(), OttEvidence.id.desc())
            .all()
        )
        for evidence in evidence_rows:
            evidence_by_movie.setdefault(evidence.movie_id, evidence)
        requested_titles: dict[str, set[int | None]] = {}
        request_rows = (
            self.db.query(MovieRequest.movie_name, MovieRequest.release_year)
            .filter(MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]))
            .all()
        )
        for name, year in request_rows:
            requested_titles.setdefault(name.lower(), set()).add(year)
        for movie in movies:
            requested_years = requested_titles.get((movie.title or "").lower(), set())
            movie_year = movie.release_date.year if movie.release_date else None
            self.classify_movie(
                movie,
                now=now,
                latest_evidence=evidence_by_movie.get(movie.id),
                requested=bool(requested_years and (None in requested_years or movie_year in requested_years)),
            )
        state.cursor = movies[-1].id
        state.processed_count += len(movies)
        state.status = "RUNNING"
        state.last_success_at = now
        state.last_error = None
        self.db.commit()
        return {
            "operation": self.operation,
            "processed": len(movies),
            "cursor": state.cursor,
            "complete": False,
        }

    def counts(self) -> dict:
        release = dict(
            self.db.query(Movie.release_status_code, func.count(Movie.id))
            .group_by(Movie.release_status_code)
            .all()
        )
        eligibility = dict(
            self.db.query(Movie.ott_research_eligibility, func.count(Movie.id))
            .group_by(Movie.ott_research_eligibility)
            .all()
        )
        return {
            "movies_scanned": sum(release.values()),
            "release_status": {str(key or "UNCLASSIFIED"): value for key, value in release.items()},
            "ott_eligibility": {str(key or "UNCLASSIFIED"): value for key, value in eligibility.items()},
        }


def research_status_label(latest: OttEvidence | None, eligibility_code: str | None) -> str | None:
    if eligibility_code in {"WAITING_RELEASE", "MIN_DELAY", "METADATA_REPAIR", "TOO_OLD"}:
        return ELIGIBILITY_LABELS[eligibility_code]
    # Canonical confirmation is the authoritative state.  A queue row is an
    # operational checkpoint and may predate a later manual/source-backed
    # confirmation, so it must never make a confirmed title look queued.
    if eligibility_code == "CONFIRMED":
        return RESEARCH_STATUS_LABELS["CONFIRMED"]
    if latest and latest.status in RESEARCH_STATUS_LABELS:
        return RESEARCH_STATUS_LABELS[latest.status]
    if eligibility_code in ELIGIBILITY_LABELS:
        return ELIGIBILITY_LABELS[eligibility_code]
    return None
