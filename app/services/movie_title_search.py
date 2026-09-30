"""Shared movie-title relevance policy for local and live-provider search."""

import re


SEARCH_FUZZY_THRESHOLD = 0.40
SEARCH_MIN_TRIGRAM_LENGTH = 3
SEARCH_STRONG_FUZZY_THRESHOLD = 0.60
SEARCH_FUZZY_MAX_GAP = 0.15


def normalize_title_query(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def trigram_similarity(left: str, right: str) -> float:
    def values(text: str) -> set[str]:
        padded = "  " + "  ".join(normalize_title_query(text).split())
        return {padded[index : index + 3] for index in range(max(0, len(padded) - 2))}

    a, b = values(left), values(right)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return len(a & b) / len(a | b)


def title_relevance_score(
    query: str, title: str | None, original_title: str | None = None
) -> int:
    """Mirror the SQL engine's exact > prefix > gated-fuzzy title policy."""
    needle = normalize_title_query(query)
    candidates = [normalize_title_query(title), normalize_title_query(original_title)]
    candidates = [value for value in candidates if value]
    if not needle or not candidates:
        return 0
    if candidates[0] == needle:
        return 10_000
    if len(candidates) > 1 and candidates[1] == needle:
        return 9_500
    if candidates[0].startswith(needle):
        return 8_000
    if len(candidates) > 1 and candidates[1].startswith(needle):
        return 7_500
    if len(needle) < SEARCH_MIN_TRIGRAM_LENGTH:
        return 0
    similarity = max(trigram_similarity(needle, value) for value in candidates)
    return 5_000 + int(similarity * 1_999) if similarity >= SEARCH_FUZZY_THRESHOLD else 0


def rank_live_movie_results(query: str, rows: list[dict]) -> list[dict]:
    """Apply the same quality gate to TMDB results without mixing in people."""
    scored = [
        (title_relevance_score(query, row.get("title"), row.get("original_title")), index, row)
        for index, row in enumerate(rows)
    ]
    has_exact_or_prefix = any(score >= 7_500 for score, _, _ in scored)
    eligible = [
        value
        for value in scored
        if value[0] >= (7_500 if has_exact_or_prefix else 5_000)
    ]
    eligible.sort(key=lambda value: (-value[0], value[1]))
    return [row for _, _, row in eligible]
