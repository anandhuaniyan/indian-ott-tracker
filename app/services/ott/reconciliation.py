"""Fact-specific OTT reconciliation with immutable decision history."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.operations import DataQualityIssue, OttEvidence
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import OttReconciliationDecision
from app.services.ott.providers.base import normalize_availability_type
from app.services.ott_providers import normalize_platform


SOURCE_AUTHORITY = {
    "MANUAL": 100,
    "OFFICIAL_PLATFORM": 100,
    "OFFICIAL_STUDIO": 95,
    "OFFICIAL_DISTRIBUTOR": 95,
    "OFFICIAL_ANNOUNCEMENT": 95,
    "OTTPLAY": 80,
    "NEWS": 80,
    "ESTABLISHED_PUBLICATION": 80,
    "STREAMING_AVAILABILITY": 75,
    "JUSTWATCH_TMDB": 75,
    "TMDB": 75,
    "WATCHMODE": 75,
    "TAVILY": 70,
    "SEARCH": 30,
    "OBSERVATION": 30,
    "UNKNOWN": 20,
}


def source_type(value: str | None) -> str:
    aliases = {
        "manual": "MANUAL",
        "official_platform": "OFFICIAL_PLATFORM",
        "official_announcement": "OFFICIAL_ANNOUNCEMENT",
        "official_studio": "OFFICIAL_STUDIO",
        "official_distributor": "OFFICIAL_DISTRIBUTOR",
        "established_publication": "ESTABLISHED_PUBLICATION",
        "ottplay": "OTTPLAY",
        "tmdb": "TMDB",
        "justwatch_tmdb": "JUSTWATCH_TMDB",
        "streaming_availability": "STREAMING_AVAILABILITY",
        "watchmode": "WATCHMODE",
        "tavily": "TAVILY",
        "search": "SEARCH",
        "observation": "OBSERVATION",
    }
    normalized = (value or "unknown").strip().replace("-", "_").upper()
    return aliases.get((value or "").strip().lower(), normalized)


def authority(row: OttEvidence) -> float:
    if row.manually_verified or source_type(row.source_type) == "MANUAL":
        return 100
    return SOURCE_AUTHORITY.get(source_type(row.source_type), 20)


def source_key(row: OttEvidence) -> str:
    return (row.source_name or urlparse(row.source_url or "").hostname or f"evidence:{row.id}").casefold()


def watch_type(value: str | None) -> str:
    return {
        "SUBSCRIPTION": "subscription",
        "FREE": "free",
        "ADS": "ads",
        "RENT": "rent",
        "BUY": "buy",
        "CHANNEL": "channel",
    }.get(normalize_availability_type(value), "unknown")


def aware(value):
    if value and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class OTTReconciliationService:
    """Reconcile platform and date as separate facts; UNKNOWN always beats a guess."""

    def __init__(self, db: Session, confirmation_threshold: float = 85):
        self.db = db
        self.threshold = confirmation_threshold

    def _canonical(self, movie_id: int, platform: str, country: str, availability_type: str):
        wanted_type = watch_type(availability_type)
        rows = self.db.query(OttAvailability).filter_by(movie_id=movie_id, country=country).all()
        matching = [
            row for row in rows
            if normalize_platform(row.provider) == platform and row.watch_type == wanted_type
        ]
        return next((row for row in matching if row.provider == platform), matching[0] if matching else None)

    def _issue(self, movie_id: int, detail: str):
        row = self.db.query(DataQualityIssue).filter_by(movie_id=movie_id, issue_type="ott_conflicting", resolved_at=None).first()
        if not row:
            self.db.add(DataQualityIssue(movie_id=movie_id, issue_type="ott_conflicting", severity="high", detail=detail))
        else:
            row.detail = detail

    def _resolve_issue(self, movie_id: int):
        now = datetime.now(timezone.utc)
        self.db.query(DataQualityIssue).filter_by(movie_id=movie_id, issue_type="ott_conflicting", resolved_at=None).update({"resolved_at": now}, synchronize_session=False)

    def _platform_groups(self, rows):
        groups = defaultdict(list)
        for row in rows:
            platform = normalize_platform(row.platform)
            fact = (row.fact_type or "RELEASE_DATE").upper()
            match_confidence = row.movie_match_confidence or 0
            if platform and fact != "DIGITAL_DATE" and match_confidence >= 70:
                row.platform = platform
                availability = normalize_availability_type(row.availability_type or "subscription")
                groups[(platform, availability)].append(row)
        return groups

    @staticmethod
    def _credible_date_rows(rows):
        return [
            row for row in rows
            if row.release_date
            and (row.fact_type or "RELEASE_DATE").upper() in {"RELEASE_DATE", "ANNOUNCEMENT"}
            and authority(row) >= 70
            and (row.date_confidence or row.confidence or 0) >= 65
        ]

    def reconcile(self, movie_id: int, country: str = "IN") -> str:
        country = country.upper()
        now = datetime.now(timezone.utc)
        rows = (
            self.db.query(OttEvidence)
            .filter(
                OttEvidence.movie_id == movie_id,
                OttEvidence.source_url.is_not(None),
                OttEvidence.rejected_at.is_(None),
                OttEvidence.country == country,
                OttEvidence.inspected_at.is_not(None),
            )
            .order_by(OttEvidence.id)
            .all()
        )
        groups = self._platform_groups(rows)
        self.db.query(OttReconciliationDecision).filter_by(movie_id=movie_id, country=country, is_current=True).update({"is_current": False}, synchronize_session=False)
        overall_state = "UNKNOWN"
        any_conflict = False
        confirmed_canonical = []

        for (platform, availability_type), group in groups.items():
            independent = {source_key(row) for row in group}
            platform_confidence = max((row.platform_confidence or row.confidence or authority(row)) for row in group)
            if len(independent) >= 2:
                platform_confidence = max(platform_confidence, min(98, platform_confidence + 5))
            preliminary_date_rows = self._credible_date_rows(group)
            preliminary_by_date = defaultdict(list)
            for row in preliminary_date_rows:
                preliminary_by_date[row.release_date].append(row)
            credible_conflict = len(preliminary_by_date) > 1
            date_can_publish = any(
                any(candidate.manually_verified or authority(candidate) >= 95 for candidate in candidates)
                or len({source_key(candidate) for candidate in candidates}) >= 2
                for candidates in preliminary_by_date.values()
            )
            availability_support = any(
                (row.fact_type or "RELEASE_DATE").upper() == "AVAILABILITY"
                and (row.platform_confidence or row.confidence or 0) >= 70
                for row in group
            )
            selected_platform = availability_support or date_can_publish or credible_conflict
            if not selected_platform:
                for row in group:
                    row.status = "POSSIBLE"
                continue
            canonical = self._canonical(movie_id, platform, country, availability_type)
            if not canonical:
                canonical = OttAvailability(movie_id=movie_id, provider=platform, country=country, watch_type=watch_type(availability_type))
                self.db.add(canonical)
                self.db.flush()
            canonical.provider = platform
            canonical.platform_confidence = platform_confidence
            canonical.confidence = max(canonical.confidence or 0, platform_confidence)
            canonical.supporting_evidence_ids = [row.id for row in group]
            observation_times = [aware(row.observed_at) for row in group if row.observed_at]
            first_times = [aware(value) for value in [canonical.first_seen_at, *observation_times] if value]
            canonical.first_seen_at = min(first_times) if first_times else None
            canonical.last_seen_at = max([aware(value) for value in [canonical.last_seen_at, *observation_times, now] if value])
            if observation_times:
                canonical.observed_available_from = min(observation_times)

            date_rows = self._credible_date_rows(group)
            by_date = defaultdict(list)
            for row in date_rows:
                by_date[row.release_date].append(row)
            manual_dates = {value for value, candidates in by_date.items() if any(row.manually_verified for row in candidates)}
            official_dates = {value for value, candidates in by_date.items() if any(authority(row) >= 95 for row in candidates)}
            credible_dates = set(by_date)
            chosen_date = None
            chosen_rows = []
            conflict_rows = []

            if manual_dates:
                chosen_date = max(manual_dates, key=lambda value: max(row.id for row in by_date[value] if row.manually_verified))
                chosen_rows = by_date[chosen_date]
                conflict_rows = [row for value, candidates in by_date.items() if value != chosen_date for row in candidates]
            elif len(official_dates) == 1:
                chosen_date = next(iter(official_dates))
                chosen_rows = by_date[chosen_date]
                for value, candidates in by_date.items():
                    if value != chosen_date:
                        for row in candidates:
                            row.status = "SUPERSEDED"
                            row.superseded_by_id = max(chosen_rows, key=lambda item: (authority(item), item.id)).id
            elif len(official_dates) > 1:
                conflict_rows = [row for candidates in by_date.values() for row in candidates]
            elif len(credible_dates) > 1:
                # Without an official/manual source, differing credible dates are
                # not guessed even when one candidate has more articles.
                conflict_rows = [row for candidates in by_date.values() for row in candidates]
            elif len(credible_dates) == 1:
                candidate_date = next(iter(credible_dates))
                candidates = by_date[candidate_date]
                if len({source_key(row) for row in candidates}) >= 2:
                    chosen_date, chosen_rows = candidate_date, candidates

            if conflict_rows:
                any_conflict = True
                for row in conflict_rows:
                    if not row.manually_verified:
                        row.status = "CONFLICTING"
                if not canonical.locked_by_admin:
                    canonical.verification_status = "NEEDS_REVIEW"
                    canonical.release_state = "CONFLICTING"
                state = "CONFLICTING"
                reason = "Credible sources disagree about the OTT date; no date was guessed"
            elif chosen_date:
                chosen = max(chosen_rows, key=lambda row: (row.manually_verified, authority(row), row.date_confidence or row.confidence or 0, row.id))
                date_confidence = 100 if chosen.manually_verified else max(
                    self.threshold,
                    min(100, max(row.date_confidence or row.confidence or authority(row) for row in chosen_rows) + (5 if len({source_key(row) for row in chosen_rows}) >= 2 else 0)),
                )
                if not canonical.locked_by_admin or chosen.manually_verified:
                    canonical.ott_release_date = chosen_date
                    canonical.date_confidence = date_confidence
                    canonical.verification_status = "CONFIRMED"
                    canonical.verification_method = "MANUAL" if chosen.manually_verified else "RECONCILIATION"
                    canonical.locked_by_admin = chosen.manually_verified
                    canonical.manually_verified = chosen.manually_verified
                    canonical.evidence_id = chosen.id
                    canonical.source_type = chosen.source_type
                    canonical.source_url = chosen.source_url
                    canonical.verified_at = now
                for row in chosen_rows:
                    row.status = "CONFIRMED"
                state = "UPCOMING_CONFIRMED" if chosen_date > now.date() else "RELEASED_CONFIRMED"
                canonical.status = "upcoming" if chosen_date > now.date() else "released"
                canonical.release_state = state
                confirmed_canonical.append(canonical)
                reason = "OTT date selected from manual, official, or independent agreeing evidence"
            else:
                for row in group:
                    if row.status not in {"SUPERSEDED", "CONFLICTING"}:
                        row.status = "POSSIBLE"
                if not canonical.locked_by_admin and not canonical.ott_release_date:
                    selected = max(
                        group,
                        key=lambda row: (
                            authority(row),
                            row.platform_confidence or row.confidence or 0,
                            row.id,
                        ),
                    )
                    canonical.verification_status = "PLATFORM_CONFIRMED" if platform_confidence >= 75 else "UNKNOWN"
                    canonical.release_state = "OBSERVED_AVAILABLE" if observation_times else "PLATFORM_ONLY"
                    canonical.evidence_id = selected.id
                    canonical.source_type = selected.source_type
                    canonical.source_url = selected.source_url
                    canonical.verification_method = selected.verification_method
                    canonical.verified_at = now
                state = canonical.release_state or "PLATFORM_ONLY"
                reason = "India platform availability is known; original OTT date is not confirmed"

            match_confidence = max((row.movie_match_confidence or 0) for row in group)
            source_count = len(independent)
            recent = any(aware(row.last_checked) and aware(row.last_checked) >= now - timedelta(days=30) for row in group)
            health = min(100, (20 if match_confidence >= 85 else 10) + 25 + (25 if canonical.ott_release_date and canonical.verification_status == "CONFIRMED" else 0) + 10 + (10 if source_count >= 2 else 0) + (10 if recent else 0))
            canonical.health_score = health
            self.db.add(
                OttReconciliationDecision(
                    movie_id=movie_id,
                    country=country,
                    state=state,
                    platform=platform,
                    release_date=canonical.ott_release_date if canonical.verification_status == "CONFIRMED" else None,
                    availability_type=availability_type,
                    platform_confidence=platform_confidence,
                    date_confidence=canonical.date_confidence or 0,
                    movie_match_confidence=match_confidence,
                    health_score=health,
                    reason=reason,
                    supporting_evidence_ids=[row.id for row in group],
                    conflicting_evidence_ids=[row.id for row in conflict_rows],
                    decided_at=now,
                    is_current=True,
                )
            )
            if state in {"RELEASED_CONFIRMED", "UPCOMING_CONFIRMED"}:
                overall_state = state
            elif overall_state == "UNKNOWN":
                overall_state = state

        if overall_state == "UNKNOWN" and rows:
            overall_state = "POSSIBLE"
        if any_conflict:
            overall_state = "CONFLICTING"
            self._issue(movie_id, "Credible OTT evidence disagrees for the same India platform")
        else:
            self._resolve_issue(movie_id)

        if confirmed_canonical:
            earliest = min(confirmed_canonical, key=lambda row: (row.ott_release_date, row.id))
            for row in self.db.query(OttAvailability).filter_by(movie_id=movie_id, country=country).all():
                row.is_original_premiere = row.id == earliest.id
        self.db.flush()
        return overall_state
