"""Deterministic advertising-quality decisions, independent of indexability."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AdsQualityDecision:
    eligible: bool
    reason: str
    score: int


def evaluate_movie_ads_quality(movie, *, credits=(), releases=(), trailer=None) -> AdsQualityDecision:
    """Score durable content signals without changing public/indexing policy."""
    if bool(getattr(movie, "adult", False)) or getattr(movie, "safety_public_visibility", "FULL") != "FULL":
        return AdsQualityDecision(False, "UNSAFE_CONTENT", 0)
    overview = (getattr(movie, "overview", None) or "").strip()
    ott = list(getattr(movie, "ott_availabilities", ()) or ())
    ratings = list(getattr(movie, "ratings", ()) or ())
    metadata = sum(bool(getattr(movie, key, None)) for key in (
        "runtime_minutes", "release_date", "original_language", "status", "tagline",
    ))
    verified_ott = any(getattr(row, "verification_status", "") == "CONFIRMED" for row in ott)
    confirmed_date = any(
        getattr(row, "verification_status", "") == "CONFIRMED" and getattr(row, "ott_release_date", None)
        for row in ott
    )
    signals = {
        "overview": 2 if len(overview) >= 120 else 1 if len(overview) >= 50 else 0,
        "artwork": 1 if getattr(movie, "poster_path", None) else 0,
        "credits": 2 if len(credits) >= 4 else 1 if credits else 0,
        "release_history": 1 if releases or getattr(movie, "release_date", None) else 0,
        "verified_ott": 2 if verified_ott else 0,
        "confirmed_ott_date": 1 if confirmed_date else 0,
        "trailer": 1 if trailer else 0,
        "rating": 1 if ratings or getattr(movie, "vote_count", 0) else 0,
        "metadata": 1 if metadata >= 3 else 0,
    }
    score = sum(signals.values())
    substantial = signals["overview"] > 0 and signals["artwork"] > 0 and score >= 6
    return AdsQualityDecision(substantial, "SUBSTANTIAL_CONTENT" if substantial else "INSUFFICIENT_CONTENT", score)
