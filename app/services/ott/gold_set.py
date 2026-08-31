"""Manually curated 100-movie accuracy gate for OTT publication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.movie import Movie
from app.models.operations import OperationState
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import OttGoldSetCase
from app.services.ott_providers import normalize_platform


LANGUAGES = ("ml", "ta", "te", "hi", "kn")


class OttGoldSetService:
    def __init__(self, db: Session):
        self.db = db

    def _candidates(self, language: str):
        today = datetime.now(timezone.utc).date()
        has_ott = exists(select(OttAvailability.id).where(OttAvailability.movie_id == Movie.id))
        groups = (
            ("UPCOMING", Movie.release_date > today),
            ("RECENT", Movie.release_date.between(today - timedelta(days=180), today)),
            ("OLD", Movie.release_date < today - timedelta(days=365)),
            ("PLATFORM_ONLY", has_ott),
            ("NO_OTT", ~has_ott),
        )
        chosen = []
        seen = set()
        per_group = max(1, settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE // len(groups))
        for category, condition in groups:
            rows = (
                self.db.query(Movie)
                .filter(Movie.original_language == language, condition)
                .order_by(Movie.popularity.desc().nullslast(), Movie.id.desc())
                .limit(per_group * 3)
                .all()
            )
            for movie in rows:
                if movie.id in seen:
                    continue
                chosen.append((movie, category))
                seen.add(movie.id)
                if sum(1 for _, value in chosen if value == category) >= per_group:
                    break
        if len(chosen) < settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE:
            rows = self.db.query(Movie).filter(Movie.original_language == language).order_by(Movie.popularity.desc().nullslast(), Movie.id.desc()).limit(100).all()
            for movie in rows:
                if movie.id not in seen:
                    chosen.append((movie, "POPULAR"))
                    seen.add(movie.id)
                if len(chosen) >= settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE:
                    break
        return chosen[: settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE]

    def generate(self) -> dict:
        added = 0
        for language in LANGUAGES:
            for movie, category in self._candidates(language):
                if not self.db.query(OttGoldSetCase.id).filter_by(movie_id=movie.id).first():
                    self.db.add(OttGoldSetCase(movie_id=movie.id, language=language, category=category, expected_state="UNKNOWN"))
                    added += 1
        self.db.commit()
        return {"added": added, "total": self.db.query(OttGoldSetCase).count(), "target": settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE * len(LANGUAGES)}

    def evaluate(self) -> dict:
        cases = self.db.query(OttGoldSetCase).all()
        verified = [case for case in cases if case.manually_verified_at]
        platform_expected = platform_correct = date_expected = date_correct = false_dates = 0
        for case in verified:
            rows = self.db.query(OttAvailability).filter_by(movie_id=case.movie_id, country="IN").all()
            canonical = next((row for row in rows if row.is_original_premiere), None) or next((row for row in rows if row.verification_status == "CONFIRMED"), None) or (rows[0] if rows else None)
            if case.expected_platform:
                platform_expected += 1
                if canonical and normalize_platform(canonical.provider) == normalize_platform(case.expected_platform):
                    platform_correct += 1
            if case.expected_release_date:
                date_expected += 1
                if canonical and canonical.verification_status == "CONFIRMED" and canonical.ott_release_date == case.expected_release_date:
                    date_correct += 1
            if canonical and canonical.verification_status == "CONFIRMED" and canonical.ott_release_date and not case.expected_release_date:
                false_dates += 1
        platform_precision = platform_correct / platform_expected if platform_expected else None
        date_precision = date_correct / date_expected if date_expected else None
        target = settings.OTT_GOLD_SET_SIZE_PER_LANGUAGE * len(LANGUAGES)
        gate_passed = bool(
            len(verified) >= target
            and platform_precision is not None and platform_precision >= 0.95
            and (date_precision is None or date_precision >= 0.98)
            and false_dates == 0
        )
        result = {
            "total": len(cases),
            "verified": len(verified),
            "target": target,
            "platform_precision": platform_precision,
            "date_precision": date_precision,
            "false_dates": false_dates,
            "gate_passed": gate_passed,
            "automatic_publication_enabled": settings.OTT_INTELLIGENCE_AUTO_PUBLICATION_ENABLED,
        }
        state = self.db.query(OperationState).filter_by(name="ott.gold_set_accuracy").first()
        if not state:
            state = OperationState(name="ott.gold_set_accuracy")
            self.db.add(state)
        state.status = "COMPLETE" if gate_passed else "BLOCKED"
        state.total_count = target
        state.processed_count = len(verified)
        state.details = result
        state.last_success_at = datetime.now(timezone.utc)
        self.db.commit()
        return result
