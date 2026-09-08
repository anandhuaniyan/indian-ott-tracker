"""SEO support: robots.txt, ads.txt and chunked sitemap generation.

Sitemaps are split into an index because the live catalog (movies + cast
members) exceeds Google's 50,000-URL per-file limit.
"""

from math import ceil
from urllib.parse import quote
from xml.sax.saxutils import escape

SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"

# People pages are chunked well under Google's 50,000-URL cap per file.
SITEMAP_PEOPLE_PER_FILE = 40000


def robots_txt(site_url: str) -> str:
    """Origin robots.txt. Legitimate crawlers may access all public content.

    The public read-only API (/api/v1) must stay crawlable: Googlebot's renderer
    fetches it as a subresource while executing the SPA, and blocking it makes
    every page render as "Unable to load this page" (a soft 404).  Only the
    private admin API and deep-search endpoints remain disallowed.
    """
    base = site_url.rstrip("/")
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/v1/admin\n"
        "Disallow: /api/v1/deep-search\n"
        "Disallow: /admin\n"
        "Disallow: /deep-search\n"
        "Disallow: /*?mode=deep\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


def ads_txt(publisher_id: str) -> str:
    """Official Google seller record; empty until a real publisher ID is set."""
    if not publisher_id:
        return ""
    return f"google.com, {publisher_id}, DIRECT, f08c47fec0942fa0\n"


def urlset(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="{SITEMAP_NAMESPACE}">{body}</urlset>'
    )


def sitemapindex(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sitemapindex xmlns="{SITEMAP_NAMESPACE}">{body}</sitemapindex>'
    )


def static_urls(site_url: str, db) -> list[str]:
    """Public landing and filter-level pages that are intentionally indexable."""
    base = site_url.rstrip("/")
    from app.models.genre import Genre
    from app.models.ott_availability import OttAvailability
    from app.services.ott_providers import normalize_platform

    paths = [
        "/",
        "/discover",
        "/ott",
        "/about",
        "/contact",
        "/privacy",
        "/terms",
        "/cookies",
    ]
    paths += [
        f"/calendar/{period}"
        for period in (
            "previous-week",
            "this-week",
            "next-week",
            "previous-month",
            "this-month",
            "next-month",
        )
    ]
    paths += [f"/languages/{code}" for code in ("ml", "ta", "te", "hi", "kn")]
    paths += [f"/genres/{quote(row.slug, safe='-')}" for row in db.query(Genre.slug).filter(Genre.movies.any()).order_by(Genre.slug)]
    paths += [
        f"/ott/{quote(platform.lower().replace(' ', '-'), safe='-+')}"
        for row in db.query(OttAvailability.provider).distinct().order_by(OttAvailability.provider)
        if (platform := normalize_platform(row[0]))
    ]
    return list(dict.fromkeys(f"{base}{path}" for path in paths))


def people_file_count(people_count: int) -> int:
    return max(1, ceil(people_count / SITEMAP_PEOPLE_PER_FILE))


def url_row(loc: str, lastmod: str | None = None) -> str:
    tail = f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else ""
    return f"<url><loc>{escape(loc)}</loc>{tail}</url>"
