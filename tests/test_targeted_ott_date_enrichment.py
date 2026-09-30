from datetime import date

from app.services.ott.targeted_date_enrichment import (
    normalized_title,
    is_legal_watch_url,
    parse_curated_page,
    parse_release_date,
    split_platforms,
)


def test_curated_table_parser_extracts_only_explicit_dates():
    html = b"""
    <html><head><title>OTT dates</title></head><body><table>
      <tr><th>Movie</th><th>Platform</th><th>OTT Release Date</th></tr>
      <tr><td>Exact Film (Malayalam)</td><td>Prime Video &amp; Sun NXT</td><td>22 May 2026</td></tr>
      <tr><td>Undated Film</td><td>Netflix</td><td>Soon</td></tr>
    </table></body></html>
    """
    rows = parse_curated_page(
        html,
        source_url="https://example.test/releases",
        source_title="OTT dates",
        default_language="ml",
    )
    assert [(row.platform, row.release_date) for row in rows] == [
        ("Prime Video", date(2026, 5, 22)),
        ("Sun NXT", date(2026, 5, 22)),
    ]
    assert normalized_title(rows[0].title) == "exact film"


def test_date_and_platform_parsers_do_not_guess():
    assert parse_release_date("Soon") is None
    assert parse_release_date("May 2026") is None
    assert parse_release_date("13 Mar 2026") == date(2026, 3, 13)
    assert split_platforms("Watch on JioHotstar & ZEE5") == ("JioHotstar", "ZEE5")


def test_language_column_does_not_leak_unrecognized_rows_into_default_language():
    html = b"""
    <table>
      <tr><th>Movie</th><th>Language</th><th>Platform</th><th>OTT Date</th></tr>
      <tr><td>Kannada Film</td><td>Kannada</td><td>Prime Video</td><td>1 Sep 2026</td></tr>
      <tr><td>Bengali Film</td><td>Bengali</td><td>Hoichoi</td><td>2 Sep 2026</td></tr>
    </table>
    """
    rows = parse_curated_page(
        html,
        source_url="https://example.test/releases",
        source_title="Mixed OTT dates",
        default_language="kn",
    )
    assert [row.language for row in rows] == ["kn", "__unmatched__"]


def test_legal_watch_url_excludes_editorial_evidence_pages():
    assert is_legal_watch_url("https://www.themoviedb.org/movie/123/watch?locale=IN")
    assert is_legal_watch_url("https://www.primevideo.com/detail/example")
    assert not is_legal_watch_url("https://cinebuds.com/telugu-movies-digital-release-dates/")
    assert not is_legal_watch_url("https://www.themoviedb.org/movie/123/releases")
