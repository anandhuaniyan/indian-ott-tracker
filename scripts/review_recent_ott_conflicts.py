"""Auditable review/apply step for the six 2026 OTT-date conflicts."""

import argparse
from datetime import date

from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.operations import DataQualityIssue, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.ott.targeted_date_enrichment import CuratedRelease, TargetedOttDateEnrichmentService
from app.services.ott_providers import normalize_platform


REVIEWS = (
    (121, "Oru Vadakkan Velicham - A Northern Light", "ManoramaMAX", "2026-07-11", "keralatv.in", "2026-07-15", "cinebuds.com", "LEAVE_CONFLICT"),
    (6242, "Kenatha Kanom", "JioHotstar", "2026-06-15", "cinebuds.com", "2026-06-16", "JioHotstar Tamil (official)", "CONFIRM_2026-06-16"),
    (6350, "Ramani Kalyanam", "Prime Video / ETV Win / Sun NXT", "2026-06-04", "cinebuds.com", "2026-06-12", "Economic Times / Cinema Express / Gadgets 360", "CONFIRM_2026-06-12"),
    (6353, "Ugly Story", "Prime Video / JioHotstar / aha", "2026-06-04", "cinebuds.com", "2026-06-05", "TMDB / Cinebuds / Trendraja / Nettv4u", "CONFIRM_2026-06-05"),
    (12336, "Hushar Pittalu", "Prime Video", "2026-09-05", "Hindustan Times", "2026-09-15", "Filmibeat / ABP / TV9 / JustWatch", "CONFIRM_2026-09-15"),
    (12384, "Love Seasons", "Prime Video", "2026-08-27", "NewsroomPost", "2026-08-30", "owner list / Cinebuds", "LEAVE_CONFLICT"),
)


def _reject(research: OttResearchService, evidence_id: int, movie_id: int, expected_date: date, reason: str) -> None:
    row = research.db.get(OttEvidence, evidence_id)
    if not row or row.movie_id != movie_id or row.release_date != expected_date:
        raise RuntimeError(f"evidence guard failed for {evidence_id}")
    if not row.rejected_at:
        research.reject_evidence(evidence_id, reason)


def _manual(targeted: TargetedOttDateEnrichmentService, movie: Movie, platform: str, release_date: date, source_url: str, source_title: str) -> None:
    targeted._save_entry(
        movie,
        CuratedRelease(movie.title, platform, release_date, source_url, source_title, movie.original_language),
        manual=True,
    )


def apply_resolutions(db) -> None:
    research = OttResearchService(db)
    targeted = TargetedOttDateEnrichmentService(db)

    kenatha = db.get(Movie, 6242)
    _reject(research, 23842, 6242, date(2026, 6, 15), "Superseded by the verified official JioHotstar Tamil announcement for June 16")
    _manual(targeted, kenatha, "JioHotstar", date(2026, 6, 16), "https://www.youtube.com/watch?v=fmfooM3v2AU", "JioHotstar Tamil: Streaming from June 16")

    ramani = db.get(Movie, 6350)
    _reject(research, 23929, 6350, date(2026, 6, 4), "Superseded by independent June 12 reports and the Sun NXT announcement")
    for platform in ("Prime Video", "ETV Win", "Sun NXT"):
        _manual(targeted, ramani, platform, date(2026, 6, 12), "https://ntvtelugu.com/movie-news/telugu-romantic-drama-ramani-kalyanam-now-streaming-on-prime-video-etv-win-sun-nxt-980672.html", "Ramani Kalyanam streaming on three platforms from June 12")

    for evidence_id in (23904, 23905, 23906):
        _reject(research, evidence_id, 6353, date(2026, 6, 4), "Superseded by multiple independent June 5 listings")
    research.evaluate_movie(6353)
    db.commit()

    hushar = db.get(Movie, 12336)
    _reject(research, 13472, 12336, date(2026, 9, 5), "Superseded by multiple current publications and observed Prime Video availability on September 15")
    for source_url, source_name, source_title in (
        ("https://www.filmibeat.com/telugu/movies/hushar-pittalu/ott.html", "filmibeat.com", "Hushar Pittalu streaming on Prime Video from September 15"),
        ("https://telugu.abplive.com/entertainment/ott-webseries/husharu-pittalu-ott-streaming-where-to-watch-youthful-teenage-romance-online-amazon-prime-video-258945", "telugu.abplive.com", "Hushar Pittalu now streaming on Prime Video September 15"),
    ):
        exists = db.query(OttEvidence.id).filter_by(
            movie_id=hushar.id,
            platform="Prime Video",
            release_date=date(2026, 9, 15),
            source_url=source_url,
        ).first()
        if not exists:
            research.record_evidence(
                hushar.id,
                platform="Prime Video",
                release_date=date(2026, 9, 15),
                source_url=source_url,
                source_title=source_title,
                source_name=source_name,
                source_type="established_publication",
                country="IN",
                inspected=True,
                confidence=88,
                platform_confidence=90,
                date_confidence=90,
                movie_match_confidence=100,
                verification_method="MANUAL_WEB_RESEARCH",
                research_run_id="conflict-review-2026-09-20",
            )
    research.evaluate_movie(12336)
    db.commit()

    # Reconciliation stores the evidence page as provenance.  The public
    # availability field is reserved for an actual legal watch destination, so
    # restore the known TMDB watch links and clear editorial article URLs.
    legal_urls = {
        (6242, "JioHotstar"): None,
        (6350, "Prime Video"): None,
        (6350, "ETV Win"): None,
        (6350, "Sun NXT"): "https://www.themoviedb.org/movie/1703675-ramani-kalyanam/watch?locale=IN",
        (6353, "Prime Video"): "https://www.themoviedb.org/movie/1439584-ugly-story/watch?locale=IN",
        (6353, "JioHotstar"): None,
        (6353, "aha"): None,
        (12336, "Prime Video"): "https://www.themoviedb.org/movie/1742434/watch?locale=IN",
    }
    for row in db.query(OttAvailability).filter(
        OttAvailability.movie_id.in_({movie_id for movie_id, _ in legal_urls}),
        OttAvailability.country == "IN",
    ):
        key = (row.movie_id, normalize_platform(row.provider))
        if key in legal_urls:
            row.source_url = legal_urls[key]
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for review in REVIEWS:
        print(
            f"MOVIE: {review[1]} | EXISTING_PLATFORM: {review[2]} | "
            f"DATE_CANDIDATE_1: {review[3]} | SOURCE_1: {review[4]} | "
            f"DATE_CANDIDATE_2: {review[5]} | SOURCE_2: {review[6]} | "
            f"RECOMMENDED_ACTION: {review[7]}"
        )
    if not args.apply:
        return
    db = SessionLocal()
    try:
        apply_resolutions(db)
        unresolved = (
            db.query(DataQualityIssue)
            .filter_by(issue_type="ott_conflicting", resolved_at=None)
            .count()
        )
        print(f"UNRESOLVED_CONFLICTS: {unresolved}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
