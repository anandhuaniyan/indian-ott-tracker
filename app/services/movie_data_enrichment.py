"""Resumable, provider-isolated catalogue enrichment."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_
from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieRating
from app.models.operations import BackfillRecord, OperationState
from app.models.ott_availability import OttAvailability
from app.services.movie_metadata_service import MovieMetadataService
from app.services.ott.intelligence import OTTIntelligenceService
from app.services.ott.web_research import WebOttResearchService
from app.services.rating_provider import ensure_pending_rating
from app.services.tmdb.movie_service import TMDbMovieService


OPERATION = "operations.movie_data_enrichment"
CONFIRMED_OTT = ("CONFIRMED", "PLATFORM_CONFIRMED", "VERIFIED")


class MovieDataEnrichmentService:
    """Attempt every incomplete movie once per retry window and checkpoint each one."""

    operation = OPERATION

    def __init__(self, db: Session, retry_days: int = 7):
        self.db = db
        self.retry_days = max(1, retry_days)
        self.tmdb = TMDbMovieService()
        self.metadata = MovieMetadataService(db, cache_artwork=False, include_ott=False)
        self.ott = OTTIntelligenceService(db)
        self.web = WebOttResearchService(db)

    def _state(self) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if state is None:
            state = OperationState(name=self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    @staticmethod
    def _missing_imdb_id():
        return ~exists().where(
            ExternalId.movie_id == Movie.id,
            func.lower(ExternalId.provider) == "imdb",
            ExternalId.external_id.is_not(None),
            ExternalId.external_id != "",
        )

    @staticmethod
    def _missing_imdb_rating():
        return ~exists().where(
            MovieRating.movie_id == Movie.id,
            func.lower(MovieRating.source) == "imdb",
            MovieRating.rating.is_not(None),
            MovieRating.rating > 0,
        )

    @staticmethod
    def _missing_ott_platform():
        return ~exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.verification_status.in_(CONFIRMED_OTT),
        )

    @staticmethod
    def _missing_ott_date():
        return ~exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.verification_status.in_(CONFIRMED_OTT),
            OttAvailability.ott_release_date.is_not(None),
        )

    def _incomplete(self):
        return or_(
            self._missing_imdb_id(),
            self._missing_imdb_rating(),
            self._missing_ott_platform(),
            self._missing_ott_date(),
        )

    def _targets(self, limit: int):
        retry_after = datetime.now(timezone.utc) - timedelta(days=self.retry_days)
        recently_attempted = exists().where(
            BackfillRecord.operation == self.operation,
            BackfillRecord.entity_type == "movie",
            BackfillRecord.entity_id == Movie.id,
            or_(
                BackfillRecord.status == "DONE",
                and_(
                    BackfillRecord.status.in_(("DEFERRED", "FAILED", "RUNNING")),
                    BackfillRecord.last_attempt_at.is_not(None),
                    BackfillRecord.last_attempt_at > retry_after,
                ),
            ),
        )
        return (
            self.db.query(Movie)
            .filter(self._incomplete(), ~recently_attempted)
            .order_by(
                case((Movie.release_date <= date.today(), 0), else_=1),
                Movie.popularity.desc().nullslast(),
                Movie.release_date.desc().nullslast(),
                Movie.id,
            )
            .limit(limit)
            .all()
        )

    def _record(self, movie_id: int) -> BackfillRecord:
        record = self.db.query(BackfillRecord).filter_by(
            operation=self.operation, entity_type="movie", entity_id=movie_id
        ).first()
        if record is None:
            record = BackfillRecord(
                operation=self.operation, entity_type="movie", entity_id=movie_id
            )
            self.db.add(record)
            self.db.flush()
        return record

    def _snapshot(self, movie_id: int) -> dict:
        imdb_id = self.db.query(ExternalId.id).filter(
            ExternalId.movie_id == movie_id,
            func.lower(ExternalId.provider) == "imdb",
            ExternalId.external_id.is_not(None),
            ExternalId.external_id != "",
        ).first() is not None
        rating = self.db.query(MovieRating).filter(
            MovieRating.movie_id == movie_id,
            func.lower(MovieRating.source) == "imdb",
        ).first()
        ott_rows = self.db.query(OttAvailability).filter(
            OttAvailability.movie_id == movie_id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.verification_status.in_(CONFIRMED_OTT),
        ).all()
        return {
            "imdb_id": imdb_id,
            "rating": rating.rating if rating and rating.rating and rating.rating > 0 else None,
            "votes": rating.vote_count if rating else None,
            "ott_keys": {(r.provider, r.country, r.watch_type) for r in ott_rows},
            "ott_dates": {r.id for r in ott_rows if r.ott_release_date},
            "legal_urls": {r.id for r in ott_rows if r.source_url},
        }

    def _recover_imdb_id(self, movie: Movie) -> bool:
        if not self._snapshot(movie.id)["imdb_id"]:
            payload = self.tmdb.get_movie_external_ids(movie.tmdb_id)
            self.metadata._upsert_external_ids(movie, payload or {})
            if self._snapshot(movie.id)["imdb_id"]:
                ensure_pending_rating(self.db, movie.id)
                self.db.commit()
                return True
            self.db.rollback()
        return False

    def run_batch(self, batch_size: int = 500, batch_number: int = 1) -> dict:
        batch_size = max(1, min(int(batch_size), 500))
        state = self._state()
        total = self.db.query(func.count(Movie.id)).filter(self._incomplete()).scalar() or 0
        state.total_count = int(total)
        state.status = "RUNNING"
        state.last_error = None
        state.completed_at = None
        self.db.commit()

        targets = self._targets(batch_size)
        report = {
            "batch": batch_number, "processed": 0, "imdb_ids_added": 0,
            "imdb_ratings_added": 0, "imdb_vote_counts_updated": 0,
            "ott_platforms_added": 0, "ott_dates_added": 0, "legal_urls_added": 0,
            "existing_data_confirmed": 0, "no_verified_ott_found": 0,
            "needs_review": 0, "conflicts": 0, "source_quota_failures": 0,
            "errors": 0,
        }
        web_enabled = bool(getattr(self.web.provider, "configured", False))

        for index, movie in enumerate(targets, 1):
            before = self._snapshot(movie.id)
            record = self._record(movie.id)
            record.status = "RUNNING"
            record.attempts = (record.attempts or 0) + 1
            record.last_attempt_at = datetime.now(timezone.utc)
            record.last_error = None
            self.db.commit()
            source_errors: list[str] = []
            try:
                try:
                    report["imdb_ids_added"] += int(self._recover_imdb_id(movie))
                except Exception as exc:
                    self.db.rollback()
                    source_errors.append(f"tmdb_external_ids:{sanitize_error(exc)}")

                ott_result = self.ott.refresh_movie(
                    movie.id, provider_names={"tmdb_justwatch"}
                )
                tmdb_status = ott_result.get("providers", {}).get(
                    "tmdb_justwatch", {}
                ).get("status")
                if tmdb_status not in (None, "HEALTHY"):
                    source_errors.append(f"tmdb_justwatch:{tmdb_status}")

                current = self._snapshot(movie.id)
                year = movie.release_date.year if movie.release_date else 0
                if web_enabled and year >= 2024 and not current["ott_dates"]:
                    web_result = self.web.research_movie(movie.id, max_queries=3)
                    if web_result.get("error") or web_result.get("temporary_failure"):
                        source_errors.append(f"web:{web_result.get('error') or web_result.get('status')}")
                        report["source_quota_failures"] += 1
                        web_enabled = False

                after = self._snapshot(movie.id)
                report["imdb_ratings_added"] += int(not before["rating"] and bool(after["rating"]))
                report["imdb_vote_counts_updated"] += int(before["votes"] != after["votes"] and after["votes"] is not None)
                report["ott_platforms_added"] += len(after["ott_keys"] - before["ott_keys"])
                report["ott_dates_added"] += len(after["ott_dates"] - before["ott_dates"])
                report["legal_urls_added"] += len(after["legal_urls"] - before["legal_urls"])
                report["existing_data_confirmed"] += int(before == after and any((after["imdb_id"], after["rating"], after["ott_keys"])))
                if not after["ott_keys"]:
                    report["no_verified_ott_found"] += 1
                unresolved = not all((after["imdb_id"], after["rating"], after["ott_keys"], after["ott_dates"]))
                record = self._record(movie.id)
                record.status = "DEFERRED" if unresolved else "DONE"
                record.last_error = "; ".join(source_errors)[:2000] or ("UNRESOLVED" if unresolved else None)
                state = self._state()
                state.cursor = movie.id
                state.processed_count = (state.processed_count or 0) + 1
                report["processed"] += 1
                self.db.commit()
                imdb_outcome = "IMDb complete" if after["rating"] else ("IMDb ID verified; rating pending" if after["imdb_id"] else "IMDb unresolved")
                ott_outcome = "OTT verified" if after["ott_keys"] else "OTT unresolved"
                print(f"{index}/{len(targets)} — {movie.title} — {imdb_outcome} — {ott_outcome}", flush=True)
            except Exception as exc:
                self.db.rollback()
                record = self._record(movie.id)
                record.status = "FAILED"
                record.last_attempt_at = datetime.now(timezone.utc)
                record.last_error = sanitize_error(exc)
                report["errors"] += 1
                state = self._state()
                state.cursor = movie.id
                state.processed_count = (state.processed_count or 0) + 1
                self.db.commit()
                print(f"{index}/{len(targets)} — {movie.title} — error isolated", flush=True)

        state = self._state()
        details = dict(state.details or {})
        cumulative = dict(details.get("cumulative") or {})
        for key, value in report.items():
            if key != "batch":
                cumulative[key] = int(cumulative.get(key) or 0) + int(value)
        details.update({"last_batch": report, "cumulative": cumulative, "batch_size": batch_size})
        state.details = details
        state.last_success_at = datetime.now(timezone.utc)
        remaining_due = bool(self._targets(1))
        state.status = "RUNNING" if remaining_due else "IDLE"
        state.last_error = None
        self.db.commit()
        return report | {"remaining_due": remaining_due, "cumulative": cumulative}
