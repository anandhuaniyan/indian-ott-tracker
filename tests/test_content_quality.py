from types import SimpleNamespace

from app.services.content_quality import evaluate_movie_ads_quality


def movie(**overrides):
    values = dict(adult=False, safety_public_visibility="FULL", overview="A substantial original overview " * 8,
                  poster_path="/poster.jpg", runtime_minutes=120, release_date="2026-01-01",
                  original_language="ml", status="Released", tagline=None, ratings=[object()],
                  ott_availabilities=[], vote_count=100)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_ads_eligible_strong_movie():
    result = evaluate_movie_ads_quality(movie(), credits=[1, 2, 3, 4], releases=[1], trailer=object())
    assert result.eligible is True
    assert result.reason == "SUBSTANTIAL_CONTENT"


def test_ads_blocked_thin_movie():
    result = evaluate_movie_ads_quality(movie(overview="", ratings=[], vote_count=0), credits=[], releases=[], trailer=None)
    assert result.eligible is False
    assert result.reason == "INSUFFICIENT_CONTENT"


def test_ads_blocked_adult_movie():
    result = evaluate_movie_ads_quality(movie(adult=True), credits=[1, 2, 3, 4], releases=[1], trailer=object())
    assert result.eligible is False
    assert result.reason == "UNSAFE_CONTENT"
