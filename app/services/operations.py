"""Safe, batched operational services used by workers and admin APIs."""

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import (
    ExternalId,
    MovieCredit,
    MovieImage,
    MovieRating,
    MovieReleaseDate,
)
from app.models.operations import DataQualityIssue, OttEvidence, OperationState
from app.models.ott_availability import OttAvailability
from app.services.ott_providers import normalize_platform, source_name as source_domain
from app.services.release_status import (
    ReleaseStatusService,
    confirmed_canonical_ott,
    site_date,
)
from app.services.roles import ROLE_ALIASES

OPEN = {
    "UNKNOWN",
    "QUEUED",
    "RESEARCHING",
    "POSSIBLE",
    "CONFLICTING",
    "NOT_FOUND",
    "NEEDS_REVIEW",
    "FAILED",
}


class DataHealthService:
    def __init__(self, db: Session):
        self.db = db

    def _issue(
        self,
        movie_id: int | None,
        issue_type: str,
        severity="warning",
        detail=None,
        person_id=None,
    ):
        item = (
            self.db.query(DataQualityIssue)
            .filter_by(
                movie_id=movie_id,
                person_id=person_id,
                issue_type=issue_type,
                resolved_at=None,
            )
            .first()
        )
        if not item:
            self.db.add(
                DataQualityIssue(
                    movie_id=movie_id,
                    person_id=person_id,
                    issue_type=issue_type,
                    severity=severity,
                    detail=detail,
                )
            )
        else:
            item.severity, item.detail = severity, detail

    def _resolve(self, movie_id: int | None, issue_type: str, person_id=None):
        self.db.query(DataQualityIssue).filter_by(
            movie_id=movie_id,
            person_id=person_id,
            issue_type=issue_type,
            resolved_at=None,
        ).update({"resolved_at": datetime.now(timezone.utc)})

    def _set(self, movie_id, checks):
        for issue, value in checks.items():
            broken, severity, detail = (
                value if isinstance(value, tuple) else (value, "warning", None)
            )
            if broken:
                self._issue(movie_id, issue, severity, detail)
            else:
                self._resolve(movie_id, issue)

    def scan(self, batch_size=250):
        """Scan a bounded batch; repeated invocations eventually cover all rows."""
        state = self.db.query(OperationState).filter_by(name="data_health").first()
        if not state:
            state = OperationState(name="data_health")
            self.db.add(state)
            self.db.flush()
        movies = (
            self.db.query(Movie)
            .filter(Movie.id > state.cursor)
            .order_by(Movie.id)
            .limit(batch_size)
            .all()
        )
        if not movies:
            state.cursor = 0
            self.db.commit()
            return {"scanned": 0, "created_or_open": 0, "cycle_complete": True}
        counts = {"scanned": len(movies), "created_or_open": 0}
        release_service = ReleaseStatusService(self.db)
        today = site_date()
        for movie in movies:
            _, eligibility, latest_evidence = release_service.classify_movie(movie)
            checks = {
                "missing_poster": not movie.poster_path,
                "missing_backdrop": not movie.backdrop_path,
                "missing_title": not movie.title,
                "missing_release_date": not movie.release_date,
                "missing_language": not movie.original_language,
                "missing_genre": not movie.genres,
            }
            credits = self.db.query(MovieCredit).filter_by(movie_id=movie.id).all()
            checks["missing_cast"] = not any(c.credit_type == "cast" for c in credits)
            checks["missing_director"] = not any(
                (c.job or "").lower() == "director" for c in credits
            )
            checks["missing_cinematographer"] = not any(
                (c.job or "").lower() in ROLE_ALIASES["cinematography"] for c in credits
            )
            external_ids = self.db.query(ExternalId).filter_by(movie_id=movie.id).all()
            imdb_id = next(
                (x for x in external_ids if x.provider.lower() in {"imdb", "imdb_id"}),
                None,
            )
            checks["missing_imdb"] = imdb_id is None
            checks["missing_external_ids"] = not external_ids
            imdb_rating = (
                self.db.query(MovieRating)
                .filter(
                    MovieRating.movie_id == movie.id,
                    func.lower(MovieRating.source) == "imdb",
                )
                .first()
            )
            checks["missing_imdb_rating"] = bool(
                imdb_id and (not imdb_rating or imdb_rating.rating is None)
            )
            imdb_lifecycle = {
                "imdb_rating_pending": bool(
                    imdb_id and (not imdb_rating or imdb_rating.status == "PENDING")
                ),
                "imdb_rating_not_yet_rated": bool(
                    imdb_rating and imdb_rating.status == "NOT_YET_RATED"
                ),
                "imdb_rating_provider_failure": bool(
                    imdb_rating
                    and imdb_rating.status
                    in {"TEMPORARY_FAILURE", "NOT_FOUND", "INVALID_ID"}
                ),
                "imdb_rating_quota_blocked": bool(
                    imdb_rating and imdb_rating.status == "BLOCKED_BY_QUOTA"
                ),
            }
            for issue_type, active in imdb_lifecycle.items():
                if active:
                    self._issue(
                        movie.id,
                        issue_type,
                        "warning" if "failure" in issue_type else "info",
                        imdb_rating.status if imdb_rating else "PENDING",
                    )
                else:
                    self._resolve(movie.id, issue_type)
            releases = (
                self.db.query(MovieReleaseDate).filter_by(movie_id=movie.id).all()
            )
            checks["missing_certification"] = not any(x.certification for x in releases)
            checks["invalid_dates"] = bool(
                movie.release_date
                and (
                    movie.release_date.year < 1888
                    or movie.release_date
                    > today + timedelta(days=3650)
                )
            )
            checks["missing_logo"] = (
                not self.db.query(MovieImage.id)
                .filter_by(movie_id=movie.id, image_type="logo")
                .first()
            )
            canonical = confirmed_canonical_ott(movie)
            checks["missing_ott_provider"] = (
                eligibility.code == "ELIGIBLE" and not movie.ott_availabilities
            )
            checks["missing_ott_release_date"] = (
                eligibility.code == "ELIGIBLE"
                and bool(movie.ott_availabilities)
                and not any(x.ott_release_date for x in movie.ott_availabilities)
            )
            checks["ott_conflicts"] = (
                self.db.query(OttEvidence.id)
                .filter_by(movie_id=movie.id, status="CONFLICTING")
                .first()
                is not None
            )
            checks["ott_research_failures"] = bool(
                latest_evidence
                and latest_evidence.status == "FAILED"
                and eligibility.code
                not in {"WAITING_RELEASE", "MIN_DELAY", "METADATA_REPAIR"}
            )
            ott_rows = list(movie.ott_availabilities)
            normalized_keys = [
                (normalize_platform(row.provider), row.country, row.watch_type)
                for row in ott_rows
            ]
            checks["ott_duplicate_platform"] = len(normalized_keys) != len(
                set(normalized_keys)
            )
            checks["ott_date_without_evidence"] = any(
                row.ott_release_date
                and not row.evidence_id
                and not row.manually_verified
                for row in ott_rows
            )
            checks["ott_provider_date_misuse"] = any(
                row.ott_release_date
                and (row.source_type or "").lower()
                in {"tmdb", "themoviedb", "metadata"}
                for row in ott_rows
            )
            checks["ott_future_marked_released"] = any(
                row.ott_release_date
                and row.ott_release_date > today
                and row.status.lower() == "released"
                for row in ott_rows
            )
            checks["ott_past_marked_upcoming"] = any(
                row.ott_release_date
                and row.ott_release_date <= today
                and row.status.lower() == "upcoming"
                for row in ott_rows
            )
            checks["ott_invalid_release_date"] = any(
                row.ott_release_date
                and (
                    row.ott_release_date.year < 1990
                    or row.ott_release_date > today + timedelta(days=3650)
                )
                for row in ott_rows
            )
            checks["ott_needs_review"] = any(
                row.verification_status == "NEEDS_REVIEW" for row in ott_rows
            )
            lifecycle = {
                "ott_not_yet_expected": eligibility.code
                in {"WAITING_RELEASE", "MIN_DELAY"},
                "ott_metadata_repair": eligibility.code == "METADATA_REPAIR",
                "ott_research_pending": eligibility.code == "ELIGIBLE",
                "ott_researching": bool(
                    latest_evidence and latest_evidence.status == "RESEARCHING"
                ),
                "ott_confirmed": canonical is not None,
                "ott_not_found": bool(
                    latest_evidence and latest_evidence.status == "NOT_FOUND"
                ),
                "ott_conflicting": bool(
                    latest_evidence and latest_evidence.status == "CONFLICTING"
                ),
            }
            for issue_type, active in lifecycle.items():
                if active:
                    severity = "high" if issue_type == "ott_conflicting" else "info"
                    self._issue(movie.id, issue_type, severity, eligibility.label)
                else:
                    self._resolve(movie.id, issue_type)
            checks["duplicate_candidate"] = (
                self.db.query(Movie.id)
                .filter(
                    Movie.id != movie.id,
                    func.lower(Movie.title) == (movie.title or "").lower(),
                    Movie.release_date == movie.release_date,
                )
                .first()
                is not None
            )
            for issue, broken in checks.items():
                if broken:
                    self._issue(
                        movie.id,
                        issue,
                        (
                            "high"
                            if issue
                            in {"missing_title", "invalid_dates", "ott_conflicts"}
                            else "warning"
                        ),
                    )
                    counts["created_or_open"] += 1
                else:
                    self._resolve(movie.id, issue)
        state.cursor = movies[-1].id
        state.processed_count += len(movies)
        state.last_success_at = datetime.now(timezone.utc)
        state.last_error = None
        self.db.commit()
        return counts | {"cursor": state.cursor, "cycle_complete": False}


class ResearchUsageService:
    """Persistent free-tier guards for Tavily requests and daily movie volume."""

    def __init__(self, db: Session):
        self.db = db

    def _state(self, name: str, limit: int) -> OperationState:
        state = (
            self.db.query(OperationState).filter_by(name=name).with_for_update().first()
        )
        if not state:
            state = OperationState(name=name, total_count=limit, status="ACTIVE")
            self.db.add(state)
            self.db.flush()
        elif state.total_count != limit:
            state.total_count = limit
        return state

    def monthly_snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        day = site_date(now)
        state = self._state(
            f"tavily_usage:{day:%Y-%m}", settings.TAVILY_MONTHLY_APP_BUDGET
        )
        self.db.commit()
        return {
            "used": state.processed_count,
            "limit": state.total_count,
            "remaining": max(0, state.total_count - state.processed_count),
        }

    def reserve_tavily_query(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        day = site_date(now)
        state = self._state(
            f"tavily_usage:{day:%Y-%m}", settings.TAVILY_MONTHLY_APP_BUDGET
        )
        if state.processed_count >= state.total_count:
            state.status = "EXHAUSTED"
            self.db.commit()
            return False
        state.processed_count += 1
        state.status = (
            "EXHAUSTED" if state.processed_count >= state.total_count else "ACTIVE"
        )
        state.last_success_at = now
        self.db.commit()
        return True

    def daily_snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        state = self._state(
            f"ott_research_daily:{site_date(now).isoformat()}",
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
            f"ott_research_daily:{site_date(now).isoformat()}",
            settings.OTT_DAILY_RESEARCH_MOVIE_LIMIT,
        )
        if state.processed_count >= state.total_count:
            state.status = "EXHAUSTED"
            self.db.commit()
            return False
        state.processed_count += 1
        state.status = (
            "EXHAUSTED" if state.processed_count >= state.total_count else "ACTIVE"
        )
        state.last_success_at = now
        self.db.commit()
        return True


class OttResearchService:
    """Queue/evidence policy. Retrieval is delegated to configured lawful providers."""

    def __init__(self, db: Session, confirmation_threshold=85.0):
        self.db, self.threshold = db, confirmation_threshold

    def queue_missing(self, batch_size=100):
        # Classification is local and cheap; it must happen before any queue
        # selection so an unclassified catalogue can never become a Tavily queue.
        ReleaseStatusService(self.db).classify_batch(max(batch_size, 1000))
        movies = (
            self.db.query(Movie)
            .outerjoin(OttAvailability)
            .filter(Movie.ott_research_eligibility == "ELIGIBLE")
            .group_by(Movie.id)
            .having(
                (func.count(OttAvailability.id) == 0)
                | (
                    func.sum(
                        case(
                            (
                                and_(
                                    OttAvailability.ott_release_date.is_not(None),
                                    OttAvailability.verification_status == "CONFIRMED",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    )
                    == 0
                )
            )
            .order_by(Movie.theatrical_release_date.desc(), Movie.popularity.desc())
            .limit(batch_size)
            .all()
        )
        now = datetime.now(timezone.utc)
        added = 0
        for movie in movies:
            added += int(self.queue_movie(movie.id, now=now))
        self.db.commit()
        return added

    def queue_movie(self, movie_id: int, *, now=None):
        now = now or datetime.now(timezone.utc)
        movie = self.db.get(Movie, movie_id)
        if not movie:
            return False
        _, eligibility, latest = ReleaseStatusService(self.db).classify_movie(
            movie, now=now
        )
        if eligibility.code != "ELIGIBLE":
            return False
        active = (
            self.db.query(OttEvidence)
            .filter(
                OttEvidence.movie_id == movie_id,
                OttEvidence.source_url.is_(None),
                OttEvidence.status.in_(OPEN),
            )
            .first()
        )
        if active:
            return False
        if latest and latest.status in {
            "WAITING_RELEASE",
            "METADATA_REPAIR",
            "TOO_OLD",
        }:
            previous = latest.status
            latest.status = "QUEUED"
            latest.next_check = now
            latest.notes = f"eligibility=ELIGIBLE; previous_status={previous}"
            return True
        self.db.add(OttEvidence(movie_id=movie_id, status="QUEUED", next_check=now))
        return True

    @staticmethod
    def next_check_for(
        status: str, release_date=None, attempts=0, theatrical_date=None
    ):
        now = datetime.now(timezone.utc)
        today = site_date(now)
        days = {
            "UNKNOWN": 1,
            "POSSIBLE": 3,
            "NOT_FOUND": min(90, 7 * 2 ** min(attempts, 3)),
            "CONFLICTING": 1,
            "FAILED": min(30, 2 ** min(attempts, 5)),
            "NEEDS_REVIEW": 7,
            "QUEUED": 0,
            "RESEARCHING": 1,
        }.get(status, 30)
        if status == "CONFIRMED":
            days = 3 if release_date and release_date >= today else 30
        elif theatrical_date:
            age = (today - theatrical_date).days
            if 0 <= age <= 60:
                days = 7
            elif 61 <= age <= 120:
                days = 14
            elif 121 <= age <= 365:
                days = 30
            elif age > 365 and status == "NOT_FOUND":
                days = max(days, 90)
        return now + timedelta(days=days)

    def record_evidence(
        self,
        movie_id: int,
        *,
        platform=None,
        release_date=None,
        source_url=None,
        source_title=None,
        source_published_at=None,
        confidence=0,
        summary=None,
        source_rank="unknown",
        source_type=None,
        source_name=None,
        country="IN",
        inspected=True,
        manually_verified=False,
        trusted=False,
        fact_type=None,
        availability_type="SUBSCRIPTION",
        raw_external_id=None,
        movie_match_confidence=90,
        platform_confidence=None,
        date_confidence=None,
        verification_method="AUTOMATED",
        observed_at=None,
        allow_publication=True,
    ):
        """Persist source evidence, then recompute the canonical result.

        A caller may store a discovery result with ``inspected=False``. Such a
        row is retained for provenance but is never confirmation evidence.
        """
        now = datetime.now(timezone.utc)
        source_type = source_type or source_rank or "unknown"
        normalized_platform = normalize_platform(platform)
        status = "CONFIRMED" if manually_verified else "POSSIBLE"
        fact_type = fact_type or ("RELEASE_DATE" if release_date else "AVAILABILITY")
        platform_confidence = (
            platform_confidence if platform_confidence is not None else (confidence if normalized_platform else 0)
        )
        date_confidence = (
            date_confidence if date_confidence is not None else (confidence if release_date else 0)
        )
        evidence = OttEvidence(
            movie_id=movie_id,
            status=status,
            platform=normalized_platform,
            release_date=release_date,
            source_url=source_url,
            source_name=source_name or source_domain(source_url),
            source_type=source_type,
            country=(country or "IN").upper(),
            source_title=source_title,
            source_published_at=source_published_at,
            confidence=100.0 if manually_verified else confidence,
            summary=summary,
            discovered_at=now,
            inspected_at=now if inspected else None,
            manually_verified=manually_verified,
            trusted=trusted or manually_verified,
            last_checked=now,
            next_check=self.next_check_for(status, release_date),
            notes=f"source_rank={source_type}",
            fact_type=fact_type,
            availability_type=availability_type,
            raw_external_id=raw_external_id,
            movie_match_confidence=100 if manually_verified else movie_match_confidence,
            platform_confidence=100 if manually_verified else platform_confidence,
            date_confidence=100 if manually_verified else date_confidence,
            verification_method="MANUAL" if manually_verified else verification_method,
            observed_at=observed_at or now,
        )
        self.db.add(evidence)
        self.db.flush()
        if allow_publication:
            self.evaluate_movie(movie_id)
        else:
            self._sync_queue_status(movie_id, "POSSIBLE")
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    @staticmethod
    def _source_key(evidence: OttEvidence) -> str:
        return evidence.source_name or (
            urlparse(evidence.source_url or "").hostname or ""
        )

    @staticmethod
    def _is_reputable(evidence: OttEvidence) -> bool:
        return evidence.trusted or evidence.source_type in {
            "official_platform",
            "official_announcement",
            "established_publication",
            "manual",
        }

    def _canonical_for(
        self, movie_id: int, platform: str, country: str
    ) -> OttAvailability | None:
        rows = (
            self.db.query(OttAvailability)
            .filter_by(movie_id=movie_id, country=country)
            .all()
        )
        matching = [
            row for row in rows if normalize_platform(row.provider) == platform
        ]
        # Legacy imports may contain both an alias and the canonical provider.
        # Prefer the exact canonical row so publishing cannot rename an alias
        # onto an already-occupied unique key.
        return next(
            (row for row in matching if row.provider == platform),
            matching[0] if matching else None,
        )

    def _publish(self, evidence: OttEvidence, confidence: float) -> OttAvailability:
        now = datetime.now(timezone.utc)
        platform = normalize_platform(evidence.platform)
        canonical = self._canonical_for(evidence.movie_id, platform, evidence.country)
        if canonical and canonical.manually_verified and not evidence.manually_verified:
            return canonical
        if (
            canonical
            and (canonical.confidence or 0) > confidence
            and not evidence.manually_verified
        ):
            return canonical
        if not canonical:
            canonical = OttAvailability(
                movie_id=evidence.movie_id,
                provider=platform,
                country=evidence.country,
                watch_type="subscription",
            )
            self.db.add(canonical)
            self.db.flush()
        canonical.provider = platform
        canonical.ott_release_date = evidence.release_date
        canonical.source_url = evidence.source_url
        canonical.source_type = (
            "manual" if evidence.manually_verified else evidence.source_type
        )
        canonical.confidence = confidence
        canonical.verification_status = "CONFIRMED"
        canonical.manually_verified = evidence.manually_verified
        canonical.evidence_id = evidence.id
        canonical.verified_at = now
        canonical.last_checked = now
        canonical.status = (
            "upcoming" if evidence.release_date > site_date(now) else "released"
        )
        return canonical

    def _sync_queue_status(self, movie_id: int, status: str) -> None:
        """Keep the operational checkpoint aligned with evaluated evidence."""
        checkpoint = (
            self.db.query(OttEvidence)
            .filter(
                OttEvidence.movie_id == movie_id,
                OttEvidence.source_url.is_(None),
            )
            .order_by(OttEvidence.updated_at.desc(), OttEvidence.id.desc())
            .first()
        )
        if not checkpoint:
            return
        now = datetime.now(timezone.utc)
        checkpoint.status = status
        checkpoint.last_checked = now
        checkpoint.next_check = self.next_check_for(status)

    def evaluate_movie(self, movie_id: int) -> str:
        """Apply official/multi-source agreement and overwrite precedence."""
        from app.services.ott.reconciliation import OTTReconciliationService

        state = OTTReconciliationService(self.db, self.threshold).reconcile(movie_id)
        queue_status = (
            "CONFIRMED"
            if state in {"UPCOMING_CONFIRMED", "RELEASED_CONFIRMED"}
            else "POSSIBLE"
            if state in {"PLATFORM_ONLY", "OBSERVED_AVAILABLE"}
            else state
        )
        self._sync_queue_status(movie_id, queue_status)
        return queue_status

    def manually_verify(
        self,
        movie_id: int,
        *,
        platform: str,
        release_date: date | None,
        source_url: str,
        source_name: str | None = None,
        country: str = "IN",
        summary: str | None = None,
    ) -> OttEvidence:
        return self.record_evidence(
            movie_id,
            platform=platform,
            release_date=release_date,
            source_url=source_url,
            source_name=source_name,
            source_type="manual",
            country=country,
            confidence=100,
            summary=summary,
            inspected=True,
            manually_verified=True,
            trusted=True,
            fact_type="ANNOUNCEMENT" if release_date else "AVAILABILITY",
            availability_type="SUBSCRIPTION",
            movie_match_confidence=100,
            platform_confidence=100,
            date_confidence=100,
            verification_method="MANUAL",
        )

    def reject_evidence(
        self, evidence_id: int, reason: str | None = None
    ) -> OttEvidence:
        evidence = self.db.get(OttEvidence, evidence_id)
        if not evidence:
            raise LookupError("OTT evidence not found")
        evidence.rejected_at = datetime.now(timezone.utc)
        evidence.rejection_reason = reason
        evidence.status = "NEEDS_REVIEW"
        self.evaluate_movie(evidence.movie_id)
        self.db.commit()
        return evidence

    def transition_release_states(self, *, today: date | None = None) -> dict:
        today = today or site_date()
        future = (
            self.db.query(OttAvailability)
            .filter(
                OttAvailability.verification_status == "CONFIRMED",
                OttAvailability.ott_release_date > today,
            )
            .update({"status": "upcoming"}, synchronize_session=False)
        )
        released = (
            self.db.query(OttAvailability)
            .filter(
                OttAvailability.verification_status == "CONFIRMED",
                OttAvailability.ott_release_date <= today,
            )
            .update({"status": "released"}, synchronize_session=False)
        )
        self.db.commit()
        return {"upcoming": future, "released": released}
