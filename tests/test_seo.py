"""Production indexing contract: robots and 10k-chunked sitemap index."""

from datetime import date, datetime, timezone
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.seo as seo_module
from app.config.settings import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.genre import Genre
from app.models.movie import Movie
from app.models.movie_metadata import MovieCredit, Person
from app.models.ott_availability import OttAvailability
from conftest import register_sqlite_trigram_similarity

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    register_sqlite_trigram_similarity(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    genre = Genre(tmdb_id=18, name="Drama", slug="drama")
    stamp = datetime(2026, 9, 20, tzinfo=timezone.utc)
    movies = [
        Movie(tmdb_id=100 + index, title=f"Film {index}", release_date=date(2026, 1, index),
              original_language="ml", genres=[genre] if index == 1 else [], updated_at=stamp)
        for index in range(1, 4)
    ]
    people = [Person(tmdb_id=200 + index, name=f"Person {index}", updated_at=stamp) for index in range(1, 5)]
    people[1].profile_path = "/profile.jpg"
    session.add_all([*movies, *people])
    session.flush()
    session.add_all([
        OttAvailability(movie_id=movies[0].id, provider="Prime Video", status="available"),
        OttAvailability(movie_id=movies[1].id, provider="Amazon Prime Video", status="available"),
        OttAvailability(movie_id=movies[2].id, provider="Netflix", status="available"),
    ])
    session.add_all([
        MovieCredit(movie_id=movies[0].id, person_id=people[0].id, credit_type="cast", character="Lead"),
        MovieCredit(movie_id=movies[1].id, person_id=people[0].id, credit_type="cast", character="Lead"),
        MovieCredit(movie_id=movies[0].id, person_id=people[1].id, credit_type="crew", department="Other", job="Thanks"),
        MovieCredit(movie_id=movies[1].id, person_id=people[2].id, credit_type="cast", character="Supporting"),
        MovieCredit(movie_id=movies[2].id, person_id=people[3].id, credit_type="crew", department="Other", job="Thanks"),
    ])
    session.commit()

    monkeypatch.setattr(settings, "SITE_URL", "https://otttracker.in")
    monkeypatch.setattr(settings, "ADSENSE_PUBLISHER_ID", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(main_module, "SITEMAP_URLS_PER_FILE", 2)
    monkeypatch.setattr(seo_module, "SITEMAP_URLS_PER_FILE", 2)

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def parse(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    return ET.fromstring(response.content)


def locs(root):
    return [node.text for node in root.findall(".//sm:loc", NS)]


def test_production_chunk_limit_is_ten_thousand():
    assert "SITEMAP_URLS_PER_FILE = 10_000" in open(seo_module.__file__, encoding="utf-8").read()


def test_sitemap_index_valid_xml(client):
    root = parse(client, "/sitemap.xml")
    assert root.tag == f"{{{NS['sm']}}}sitemapindex"
    assert locs(root) == [
        "https://otttracker.in/sitemap-static.xml",
        "https://otttracker.in/sitemap-movies-1.xml",
        "https://otttracker.in/sitemap-movies-2.xml",
        "https://otttracker.in/sitemap-people-1.xml",
        "https://otttracker.in/sitemap-people-2.xml",
    ]


def test_static_sitemap_valid_xml_and_complete(client):
    urls = locs(parse(client, "/sitemap-static.xml"))
    for url in ("/", "/discover", "/search", "/ott", "/about", "/contact",
                "/privacy", "/terms", "/cookies", "/calendar/this-week", "/genres/drama",
                "/languages/ml", "/ott/prime-video", "/ott/netflix"):
        assert f"https://otttracker.in{url}" in urls
    assert "https://otttracker.in/request-movie" not in urls
    assert "https://otttracker.in/ott/amazon-prime-video" not in urls


@pytest.mark.parametrize("family", ["movies", "people"])
def test_catalog_sitemaps_valid_xml_and_chunk_limit(client, family):
    all_urls = []
    for page, expected in ((1, 2), (2, 1)):
        root = parse(client, f"/sitemap-{family}-{page}.xml")
        urls = locs(root)
        assert root.tag == f"{{{NS['sm']}}}urlset"
        assert len(urls) == expected
        assert len(urls) <= 2
        assert all(f"/{family}/" in url for url in urls)
        all_urls.extend(urls)
    assert len(all_urls) == len(set(all_urls)) == 3
    assert client.get(f"/sitemap-{family}-3.xml").status_code == 404


def test_noindex_person_is_not_in_people_sitemap(client):
    urls = locs(parse(client, "/sitemap-people-1.xml")) + locs(parse(client, "/sitemap-people-2.xml"))
    assert "https://otttracker.in/people/4" not in urls
    response = client.get("/api/v1/people/4")
    assert response.status_code == 200
    assert response.json()["seo_indexable"] is False


def test_movie_lastmod_valid(client):
    root = parse(client, "/sitemap-movies-1.xml")
    values = [node.text for node in root.findall(".//sm:lastmod", NS)]
    assert values
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in values)
    assert all(date.fromisoformat(value) for value in values)


def test_no_duplicate_or_forbidden_or_query_urls(client):
    index = parse(client, "/sitemap.xml")
    urls = []
    for child in locs(index):
        urls.extend(locs(parse(client, child.removeprefix("https://otttracker.in"))))
    assert len(urls) == len(set(urls))
    assert all(url.startswith("https://otttracker.in/") for url in urls)
    assert all("?" not in url for url in urls)
    assert all("/admin" not in url and "/api/" not in url and "/deep-search" not in url for url in urls)


def test_robots_references_sitemap_index_and_blocks_private_routes(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "Allow: /" in body
    assert "Disallow: /api/" in body
    for path in (
        "/api/v1/maintenance",
        "/api/v1/home",
        "/api/v1/discover",
        "/api/v1/languages",
        "/api/v1/search",
        "/api/v1/ott",
        "/api/v1/people/",
        "/api/v1/calendar/",
        "/api/v1/movies/*/detail",
        "/api/v1/movies/*/public-comments",
    ):
        assert f"Allow: {path}" in body
    for path in (
        "/admin/",
        "/deep-search/",
        "/login",
        "/signup",
        "/verify-email",
        "/forgot-password",
        "/reset-password",
        "/request-movie",
        "/support",
    ):
        assert f"Disallow: {path}" in body
    assert "Sitemap: https://otttracker.in/sitemap.xml" in body
    assert all(f"Disallow: {path}" not in body for path in ("/movies/", "/people/", "/genres/", "/languages/", "/ott/", "/calendar/"))
    assert "Allow: /api/v1/admin" not in body
    assert "Allow: /api/v1/auth" not in body
    assert "Allow: /api/v1/deep-search" not in body
    assert "Allow: /api/v1/movie-requests" not in body
    assert "Allow: /api/v1/contact-requests" not in body
    assert "Allow: /api/v1/movies/*/comments" not in body


@pytest.mark.parametrize("path", ["/sitemap.xml", "/sitemap-static.xml", "/sitemap-movies-1.xml", "/sitemap-people-1.xml", "/robots.txt"])
def test_crawler_get_and_head_without_session(client, path):
    response = client.get(path, headers={"User-Agent": "Googlebot"})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    head = client.head(path)
    assert head.status_code == 200
    assert not head.content


def test_ads_txt_empty_without_publisher_id(client):
    assert client.get("/ads.txt").text == ""
