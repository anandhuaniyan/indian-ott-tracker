"""Conservative post-run audit corrections for recent-Indian enrichment."""

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import func

from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.operations import BackfillRecord, DataQualityIssue, OperationState
from app.models.ott_availability import OttAvailability
from app.services.ott.indian_recent_enrichment import OPERATION
from app.services.ott.targeted_date_enrichment import is_legal_watch_url


def main() -> None:
    db = SessionLocal()
    try:
        state = db.query(OperationState).filter_by(name=OPERATION).one()
        started_at = state.created_at
        conflict_rows = (
            db.query(DataQualityIssue, Movie)
            .join(Movie, Movie.id == DataQualityIssue.movie_id)
            .filter(
                DataQualityIssue.issue_type == "ott_conflicting",
                DataQualityIssue.resolved_at.is_(None),
                DataQualityIssue.created_at >= started_at,
            )
            .all()
        )
        conflict_ids = {issue.movie_id for issue, _movie in conflict_rows}
        conflicts_by_group: dict[tuple[int, str], int] = {}
        for _issue, movie in conflict_rows:
            release = movie.theatrical_release_date or movie.release_date
            if release:
                key = (release.year, movie.original_language)
                conflicts_by_group[key] = conflicts_by_group.get(key, 0) + 1
        if conflict_ids:
            db.query(BackfillRecord).filter(
                BackfillRecord.operation == OPERATION,
                BackfillRecord.entity_type == "movie",
                BackfillRecord.entity_id.in_(conflict_ids),
            ).update(
                {"status": "DEFERRED", "last_error": "CONFLICTING_EVIDENCE"},
                synchronize_session=False,
            )

        editorial_urls_cleared = 0
        for row in db.query(OttAvailability).filter(
            OttAvailability.updated_at >= started_at,
            OttAvailability.source_url.is_not(None),
            OttAvailability.source_url != "",
        ):
            if not is_legal_watch_url(row.source_url):
                row.source_url = None
                editorial_urls_cleared += 1

        details = dict(state.details or {})
        history = list(details.get("batch_history") or [])
        for batch in history:
            batch["conflicts"] = conflicts_by_group.get((batch.get("year"), batch.get("language")), 0)
            batch["legal_urls_added"] = 0
            batch["legal_url_hit_rate"] = 0.0
        cumulative = dict(details.get("cumulative") or {})
        cumulative["conflicts"] = len(conflict_ids)
        cumulative["legal_urls_added"] = 0
        details["batch_history"] = history
        details["cumulative"] = cumulative
        details["post_run_audit"] = {
            "new_conflicts_retained_for_review": len(conflict_ids),
            "editorial_urls_removed_from_watch_url_field": editorial_urls_cleared,
            "known_preexisting_conflicts_remaining": db.query(func.count(DataQualityIssue.id)).filter(
                DataQualityIssue.issue_type == "ott_conflicting",
                DataQualityIssue.resolved_at.is_(None),
                DataQualityIssue.created_at < started_at,
            ).scalar() or 0,
            "audited_at": datetime.now(timezone.utc).isoformat(),
        }
        state.details = details
        db.commit()
        print(f"NEW_CONFLICTS_RETAINED: {len(conflict_ids)}")
        print(f"EDITORIAL_URLS_CLEARED: {editorial_urls_cleared}")
        print(f"CUMULATIVE: {cumulative}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
