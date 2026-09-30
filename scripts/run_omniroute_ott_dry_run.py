"""Run the fixed ten-movie OmniRoute benchmark without persistent writes."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
import hashlib
import json

from sqlalchemy import select

from app.config.settings import settings
from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.operations import OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.ott.ai_research import OTTResearchAgent, ResearchDecision
from app.services.ai_research_audit import AIResearchAuditService
from app.services.ott.providers.base import normalize_availability_type
from app.services.ott_providers import normalize_platform


MOVIE_IDS = (6179, 198, 126, 6321, 36, 96, 6492, 6646, 6638, 18624)
ANSWER_STATUSES = {"CONFIRMED", "PLATFORM_CONFIRMED"}


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _hash_rows(rows) -> str:
    payload = json.dumps(
        [[_json_value(value) for value in row] for row in rows],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _database_snapshot(db) -> dict:
    canonical_columns = (
        OttAvailability.id,
        OttAvailability.movie_id,
        OttAvailability.provider,
        OttAvailability.country,
        OttAvailability.watch_type,
        OttAvailability.ott_release_date,
        OttAvailability.verification_status,
        OttAvailability.manually_verified,
        OttAvailability.locked_by_admin,
        OttAvailability.source_url,
    )
    canonical = db.execute(
        select(*canonical_columns)
        .where(OttAvailability.movie_id.in_(MOVIE_IDS))
        .order_by(OttAvailability.id)
    ).all()
    admin = db.execute(
        select(*canonical_columns)
        .where(
            (OttAvailability.manually_verified.is_(True))
            | (OttAvailability.locked_by_admin.is_(True))
        )
        .where(OttAvailability.movie_id.in_(MOVIE_IDS))
        .order_by(OttAvailability.id)
    ).all()
    evidence = db.execute(
        select(
            OttEvidence.id,
            OttEvidence.movie_id,
            OttEvidence.platform,
            OttEvidence.release_date,
            OttEvidence.country,
            OttEvidence.availability_type,
            OttEvidence.source_url,
            OttEvidence.status,
        )
        .where(OttEvidence.movie_id.in_(MOVIE_IDS))
        .order_by(OttEvidence.id)
    ).all()
    return {
        "canonical_count": len(canonical),
        "canonical_hash": _hash_rows(canonical),
        "admin_count": len(admin),
        "admin_hash": _hash_rows(admin),
        "evidence_count": len(evidence),
        "evidence_hash": _hash_rows(evidence),
    }


def _known_answers(movie: Movie) -> tuple[set[tuple], set[tuple]]:
    rows = [
        row
        for row in movie.ott_availabilities
        if row.manually_verified
        or row.locked_by_admin
        or (row.verification_status or "").upper() in ANSWER_STATUSES
    ]
    platforms = {
        (
            normalize_platform(row.provider),
            (row.country or "UNKNOWN").upper(),
            normalize_availability_type(row.watch_type),
        )
        for row in rows
        if normalize_platform(row.provider)
    }
    dates = {
        (
            normalize_platform(row.provider),
            row.ott_release_date,
            (row.country or "UNKNOWN").upper(),
            normalize_availability_type(row.watch_type),
        )
        for row in rows
        if normalize_platform(row.provider) and row.ott_release_date
    }
    return platforms, dates


def _sorted_facts(values: set[tuple]) -> list[list]:
    return [list(item) for item in sorted(values, key=lambda item: tuple(str(part or "") for part in item))]


def _source_payload(item) -> dict:
    supports = []
    if item.inspected and item.platform and not item.rejection_reasons:
        supports.append("PLATFORM")
    if item.inspected and item.release_date and not item.date_rejection_reasons:
        supports.append("DATE")
    return {
        "url": item.source_url,
        "title": item.source_title,
        "domain": item.source_domain,
        "tier": item.source_type,
        "supports": supports or ["NONE"],
        "retrieved_evidence": item.evidence_summary,
        "rejection_reasons": item.rejection_reasons + item.date_rejection_reasons,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def main() -> None:
    if settings.AI_RESEARCH_ENABLED:
        raise RuntimeError("AI_RESEARCH_ENABLED must remain false for this benchmark")
    if settings.AI_RESEARCH_PROVIDER.strip().lower() != "omniroute":
        raise RuntimeError("AI_RESEARCH_PROVIDER must be omniroute")
    if not settings.AI_RESEARCH_API_URL.rstrip("/").endswith("/v1/research"):
        raise RuntimeError("AI_RESEARCH_API_URL must target the hardened /v1/research endpoint")

    db = SessionLocal()
    audit = AIResearchAuditService(db)
    audit_run = None
    try:
        before = _database_snapshot(db)
        movies = {
            movie.id: movie
            for movie in db.query(Movie).filter(Movie.id.in_(MOVIE_IDS)).all()
        }
        if set(movies) != set(MOVIE_IDS):
            raise RuntimeError(f"Benchmark movie IDs changed; found {sorted(movies)}")

        totals = {
            "platform_tp": 0,
            "platform_fp": 0,
            "platform_fn": 0,
            "date_tp": 0,
            "date_fp": 0,
            "date_fn": 0,
            "full_correct": 0,
            "platform_only": 0,
            "date_only": 0,
            "no_reliable_result": 0,
            "provider_failures": 0,
            "needs_review": 0,
            "conflicts": 0,
            "wrong_movie": 0,
            "wrong_region": 0,
            "wrong_watch_type": 0,
            "unsupported_date": 0,
            "fabricated_source_accepted": 0,
        }
        reports = []
        audit_run = audit.create_run(
            trigger="MANUAL",
            initiated_by="controlled-10-movie-benchmark",
            mode="DRY_RUN",
            provider=settings.AI_RESEARCH_PROVIDER,
            model=settings.AI_RESEARCH_MODEL,
            movies_total=len(MOVIE_IDS),
        )
        agent = OTTResearchAgent(db, audit_service=audit)

        for movie_id in MOVIE_IDS:
            movie = movies[movie_id]
            known_platforms, known_dates = _known_answers(movie)
            report = agent.research_movie(
                movie_id,
                dry_run=True,
                research_run_id=audit_run.run_id,
            )
            accepted = [
                item for item in report.findings
                if item.inspected and item.platform and not item.rejection_reasons
            ]
            found_platforms = {
                (
                    normalize_platform(item.platform),
                    item.region,
                    normalize_availability_type(item.watch_type),
                )
                for item in accepted
            }
            found_dates = {
                (
                    normalize_platform(item.platform),
                    item.release_date,
                    item.region,
                    normalize_availability_type(item.watch_type),
                )
                for item in accepted
                if item.release_date and not item.date_rejection_reasons
            }
            platform_tp = len(found_platforms & known_platforms)
            platform_fp = len(found_platforms - known_platforms)
            platform_fn = len(known_platforms - found_platforms)
            date_tp = len(found_dates & known_dates)
            date_fp = len(found_dates - known_dates)
            date_fn = len(known_dates - found_dates)
            totals["platform_tp"] += platform_tp
            totals["platform_fp"] += platform_fp
            totals["platform_fn"] += platform_fn
            totals["date_tp"] += date_tp
            totals["date_fp"] += date_fp
            totals["date_fn"] += date_fn
            totals["wrong_region"] += sum(1 for item in accepted if item.region != "IN")
            totals["wrong_watch_type"] += sum(
                1
                for fact in found_platforms
                if not any(fact[0] == known[0] and fact[2] == known[2] for known in known_platforms)
            )
            totals["unsupported_date"] += date_fp
            totals["wrong_movie"] += sum(
                1 for item in accepted if item.identity_confidence < 85
            )
            totals["fabricated_source_accepted"] += sum(
                1 for item in accepted if not item.source_url or not item.inspected
            )

            if report.decision == ResearchDecision.DEFERRED_PROVIDER_ERROR:
                totals["provider_failures"] += 1
            elif report.decision == ResearchDecision.NO_RELIABLE_RESULT:
                totals["no_reliable_result"] += 1
            elif report.decision == ResearchDecision.NEEDS_REVIEW:
                totals["needs_review"] += 1
            elif report.decision == ResearchDecision.CONFLICT:
                totals["conflicts"] += 1

            platform_correct = platform_tp > 0 and platform_fp == 0
            date_correct = date_tp > 0 and date_fp == 0
            if platform_correct and (date_correct or not known_dates):
                totals["full_correct"] += 1
            elif platform_correct:
                totals["platform_only"] += 1
            elif date_correct:
                totals["date_only"] += 1

            top_sources = [_source_payload(item) for item in report.findings[:2]]
            reports.append({
                "local_id": report.movie_identity.local_movie_id,
                "tmdb_id": report.movie_identity.tmdb_id,
                "imdb_id": report.movie_identity.imdb_id,
                "title": report.movie_identity.title,
                "original_title": report.movie_identity.original_title,
                "release_year": report.movie_identity.release_year,
                "language": report.movie_identity.original_language,
                "production_countries": list(report.movie_identity.production_countries),
                "search_queries": report.queries,
                "sources": top_sources,
                "identity_match": any(item.inspected for item in report.findings),
                "found_platforms": _sorted_facts(found_platforms),
                "found_dates": _sorted_facts(found_dates),
                "found_regions": sorted({item.region for item in accepted}),
                "found_watch_types": sorted({item.watch_type for item in accepted}),
                "platform_confidence": max((item.platform_confidence for item in accepted), default=0),
                "date_confidence": max((item.date_confidence for item in accepted if item.release_date), default=0),
                "research_decision": report.decision.value,
                "provider": report.provider,
                "model": report.model,
                "provider_error": report.provider_error,
                "known_platforms": _sorted_facts(known_platforms),
                "known_dates": _sorted_facts(known_dates),
                "platform_match": platform_tp > 0,
                "date_match": date_tp > 0,
                "platform_tp": platform_tp,
                "platform_fp": platform_fp,
                "platform_fn": platform_fn,
                "date_tp": date_tp,
                "date_fp": date_fp,
                "date_fn": date_fn,
                "rejection_reasons": sorted({
                    reason
                    for item in report.findings
                    for reason in item.rejection_reasons + item.date_rejection_reasons
                }),
            })
            db.expire_all()

        after = _database_snapshot(db)
        immutable = before == after
        result = {
            "configuration": {
                "audit_run_id": audit_run.run_id,
                "provider": settings.AI_RESEARCH_PROVIDER,
                "model": settings.AI_RESEARCH_MODEL,
                "endpoint": settings.AI_RESEARCH_API_URL,
                "ai_research_enabled": settings.AI_RESEARCH_ENABLED,
                "dry_run": True,
            },
            "movies_tested": len(reports),
            "metrics": {
                **totals,
                "platform_precision": _ratio(totals["platform_tp"], totals["platform_tp"] + totals["platform_fp"]),
                "platform_recall": _ratio(totals["platform_tp"], totals["platform_tp"] + totals["platform_fn"]),
                "date_precision": _ratio(totals["date_tp"], totals["date_tp"] + totals["date_fp"]),
                "date_recall": _ratio(totals["date_tp"], totals["date_tp"] + totals["date_fn"]),
            },
            "database_before": before,
            "database_after": after,
            "dry_run_immutability": "PASS" if immutable else "FAIL",
            "reports": reports,
        }
        if not immutable:
            raise RuntimeError("Dry-run changed database state")
        audit.complete_run(audit_run.run_id)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, default=_json_value))
    except Exception as exc:
        if audit_run is not None:
            audit.complete_run(audit_run.run_id, status="FAILED", error=exc)
        raise
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
