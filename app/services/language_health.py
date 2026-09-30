"""Read-only language health metrics and idempotent code normalization."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.services.languages import (
    ALL_PUBLIC_LANGUAGE_CODES,
    LANGUAGE_NAMES,
    SUPPORTED_PRIMARY_LANGUAGE_CODES,
    SUPPORTED_PRIMARY_LANGUAGE_NAMES,
    normalize_language_code,
)


def language_health(db: Session) -> dict:
    """Measure original-language integrity without consulting spoken languages."""
    raw_counts = db.query(
        Movie.original_language, func.count(Movie.id)
    ).group_by(Movie.original_language).all()
    buckets = {
        code: {
            "code": code,
            "language": SUPPORTED_PRIMARY_LANGUAGE_NAMES[code],
            "total": 0,
            "correct": 0,
            "mismatch": 0,
            "unknown": 0,
        }
        for code in SUPPORTED_PRIMARY_LANGUAGE_CODES
    }
    null_count = 0
    unsupported_count = 0
    unknown_count = 0
    mismatch_count = 0
    raw_code_counts = []

    for raw_code, raw_count in raw_counts:
        count = int(raw_count)
        normalized = normalize_language_code(raw_code)
        raw_normalized = (
            str(raw_code).strip().lower() if raw_code is not None else None
        )
        raw_code_counts.append(
            {"raw_code": raw_code, "normalized_code": normalized, "count": count}
        )
        if normalized is None:
            null_count += count
            continue
        if normalized not in ALL_PUBLIC_LANGUAGE_CODES:
            unsupported_count += count
            if normalized not in LANGUAGE_NAMES:
                unknown_count += count
        if normalized in buckets:
            bucket = buckets[normalized]
            bucket["total"] += count
            if raw_normalized == normalized:
                bucket["correct"] += count
            else:
                bucket["mismatch"] += count
                mismatch_count += count
        elif raw_normalized != normalized:
            mismatch_count += count

    return {
        "total_movies": sum(int(count) for _, count in raw_counts),
        "by_language": [buckets[code] for code in SUPPORTED_PRIMARY_LANGUAGE_CODES],
        "null_original_language_count": null_count,
        "unsupported_language_count": unsupported_count,
        "unknown_language_count": unknown_count,
        "language_mismatch_count": mismatch_count,
        "raw_code_counts": sorted(
            raw_code_counts,
            key=lambda item: (-item["count"], item["normalized_code"] or ""),
        ),
    }


def normalize_stored_movie_languages(db: Session, *, commit: bool = False) -> dict:
    """Normalize aliases/case/whitespace while leaving all other fields intact."""
    before = language_health(db)
    updates = []
    rows = db.query(
        Movie.original_language, func.count(Movie.id)
    ).group_by(Movie.original_language).all()
    for raw_code, raw_count in rows:
        normalized = normalize_language_code(raw_code)
        if raw_code == normalized:
            continue
        changed = (
            db.query(Movie)
            .filter(Movie.original_language == raw_code)
            .update(
                {Movie.original_language: normalized},
                synchronize_session=False,
            )
        )
        if changed:
            updates.append(
                {"from": raw_code, "to": normalized, "movies": int(changed)}
            )
    if commit:
        db.commit()
    else:
        db.rollback()
    after = language_health(db)
    return {
        "dry_run": not commit,
        "updated_movies": sum(item["movies"] for item in updates),
        "updates": updates,
        "before": before,
        "after": after,
    }
