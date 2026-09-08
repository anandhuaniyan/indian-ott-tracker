"""SEO endpoint coverage: robots, ads, and the chunked sitemap index."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.models.movie_metadata import Person
from app.models.ott_availability import OttAvailability
from conftest import register_sqlite_trigram_similarity


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    register_sqlite_trigram_similarity(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    genre = Genre(tmdb_id=18, name="Drama", slug="drama")
    language = Language(iso_639_1="ml", english_name="Malayalam")
    person = Person(tmdb_id=10, name="Example Actor", known_for_department="Acting")
    movie = Movie(
        tmdb_id=101,
        title="Example Film",
        release_date=date.today(),
        original_language="ml",
        genres=[genre],
        languages=[language],
        updated_at=None,
    )
    session.add_all([person, movie])
    session.commit()
    session.add(OttAvailability(movie_id=movie.id, provider="Netflix", status="available"))

    monkeypatch.setattr(settings, "SITE_URL", "https://otttracker.in")
    monkeypatch.setattr(settings, "ADSENSE_PUBLISHER_ID", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_robots_txt_allows_public_api_and_blocks_private_and_admin(client):
    body = client.get("/robots.txt").text
    assert "Allow: /" in body
    disallows = [line.strip() for line in body.splitlines() if line.strip().startswith("Disallow:")]
    assert "Disallow: /api/v1/admin" in disallows
    assert "Disallow: /api/v1/deep-search" in disallows
    assert "Disallow: /admin" in disallows
    assert "Disallow: /deep-search" in disallows
    assert "Disallow: /*?mode=deep" in disallows
    assert "Disallow: /api/" not in disallows
    assert all(
        not value.startswith("Disallow: /api/")
        or value.startswith(("Disallow: /api/v1/admin", "Disallow: /api/v1/deep-search"))
        for value in disallows
    )
    assert "Sitemap: https://otttracker.in/sitemap.xml" in body


def test_ads_txt_empty_without_publisher_id(client):
    assert client.get("/ads.txt").text == ""


def test_ads_txt_contains_publisher_when_set(client, monkeypatch):
    monkeypatch.setattr(settings, "ADSENSE_PUBLISHER_ID", "pub-1234567890123456")
    body = client.get("/ads.txt").text
    assert "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0" in body


def test_sitemap_index_references_expected_files(client):
    body = client.get("/sitemap.xml").text
    assert "<sitemapindex" in body
    assert "<loc>https://otttracker.in/sitemaps/static.xml</loc>" in body
    assert "<loc>https://otttracker.in/sitemaps/movies.xml</loc>" in body
    assert "<loc>https://otttracker.in/sitemaps/people-1.xml</loc>" in body


def test_sitemap_static_includes_landing_and_partial_routes(client):
    body = client.get("/sitemaps/static.xml").text
    assert "<loc>https://otttracker.in/</loc>" in body
    assert "<loc>https://otttracker.in/discover</loc>" in body
    assert "<loc>https://otttracker.in/calendar/this-week</loc>" in body
    assert "<loc>https://otttracker.in/genres/drama</loc>" in body
    assert "<loc>https://otttracker.in/ott/netflix</loc>" in body


def test_sitemap_static_excludes_admin_and_api(client):
    body = client.get("/sitemaps/static.xml").text
    assert "/admin" not in body
    assert "/api/" not in body


def test_sitemap_movies_has_wellformed_lastmod(client):
    body = client.get("/sitemaps/movies.xml").text
    assert "<loc>https://otttracker.in/movies/" in body
    import re
    assert re.search(r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>", body)


def test_sitemap_people_chunks(client):
    body = client.get("/sitemaps/people-1.xml").text
    assert "<loc>https://otttracker.in/people/" in body
    assert "<urlset" in body


def test_sitemap_people_negative_page_clamps_to_first(client):
    body = client.get("/sitemaps/people--5.xml").text
    assert "<loc>https://otttracker.in/people/" in body


@pytest.mark.parametrize("path", ["/sitemap.xml", "/sitemaps/static.xml", "/sitemaps/movies.xml", "/sitemaps/people-1.xml", "/robots.txt"])
def test_crawler_get_and_head_without_session(client, path):
    import xml.etree.ElementTree as ET
    response = client.get(path, headers={"User-Agent": "Googlebot"})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    head = client.head(path)
    assert head.status_code == 200
    assert not head.content
    if path.endswith(".xml"):
        assert response.headers["content-type"] == "application/xml"
        assert response.content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
        root = ET.fromstring(response.content)
        assert root.tag.startswith("{http://www.sitemaps.org/schemas/sitemap/0.9}")


def test_static_excludes_utilities_and_deduplicates_provider_aliases(client):
    from app.seo import static_urls
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value = [SimpleNamespace(slug="drama")]
    db.query.return_value.distinct.return_value.order_by.return_value = [("Prime Video",), ("Amazon Prime Video",), ("Netflix",)]
    urls = static_urls("https://otttracker.in", db)
    assert len(urls) == len(set(urls))
    assert "https://otttracker.in/ott/prime-video" in urls
    assert "https://otttracker.in/ott/amazon-prime-video" not in urls
    assert all(not url.endswith(("/search", "/request-movie", "/support")) for url in urls)
