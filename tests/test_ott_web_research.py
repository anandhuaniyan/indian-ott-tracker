from datetime import date, timedelta

from app.models.movie import Movie
from app.models.ott_availability import OttAvailability
from app.services.ott.web_research import WebOttResearchService


class Search:
    configured = True
    def search(self, movie, **_):
        return [{"url": "https://www.jiohotstar.com/watch/example", "title": movie.title}]


def test_web_research_publishes_only_explicit_official_date(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    movie.release_date = date.today() - timedelta(days=45)
    database.query(OttAvailability).filter_by(movie_id=movie.id).delete()
    database.commit()
    monkeypatch.setattr(
        "app.services.ott.web_research.inspect_source",
        lambda *_: {
            "url": "https://www.jiohotstar.com/watch/example", "title": "Example Film streams from 25 August 2026",
            "source_name": "jiohotstar.com", "source_type": "official_platform", "country": "IN",
            "platform": "JioHotstar", "release_date": "2026-08-25", "confidence": 95,
            "evidence_summary": "Official platform says the film starts streaming from 25 August 2026.",
        },
    )
    report = WebOttResearchService(database, provider=Search()).run(limit=5)
    row = database.query(OttAvailability).filter_by(movie_id=movie.id, provider="JioHotstar").one()
    assert report["evidence_created"] == 1
    assert row.ott_release_date == date(2026, 8, 25)
    assert row.verification_status == "CONFIRMED"


def test_web_research_never_maps_article_date_to_ott_date(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    movie.release_date = date.today() - timedelta(days=45)
    database.query(OttAvailability).filter_by(movie_id=movie.id).delete()
    database.commit()
    monkeypatch.setattr(
        "app.services.ott.web_research.inspect_source",
        lambda *_: {"url": "https://www.jiohotstar.com/watch/example", "title": "Example Film",
                    "source_name": "jiohotstar.com", "source_type": "official_platform", "country": "IN",
                    "platform": "JioHotstar", "release_date": None, "confidence": 95,
                    "evidence_summary": "The title is currently available on the service."},
    )
    WebOttResearchService(database, provider=Search()).run(limit=5)
    row = database.query(OttAvailability).filter_by(movie_id=movie.id, provider="JioHotstar").one()
    assert row.ott_release_date is None
    assert row.verification_status == "PLATFORM_CONFIRMED"
