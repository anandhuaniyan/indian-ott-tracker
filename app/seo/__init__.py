"""SEO support for crawler policy and standards-compliant XML sitemaps."""

from math import ceil
from urllib.parse import quote
from xml.sax.saxutils import escape

from sqlalchemy import and_, case, func, or_

SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"

SITEMAP_URLS_PER_FILE = 10_000


def indexable_people_query(db):
    """People with useful public filmography or a meaningful identity signal."""
    from app.models.movie import Movie
    from app.models.movie_metadata import MovieCredit, Person

    role = func.lower(func.coalesce(MovieCredit.job, ""))
    department = func.lower(func.coalesce(MovieCredit.department, ""))
    significant_credit = or_(
        and_(
            MovieCredit.credit_type == "cast",
            MovieCredit.character.is_not(None),
            func.length(func.trim(MovieCredit.character)) > 0,
        ),
        and_(
            MovieCredit.credit_type == "crew",
            or_(
                role.like("%director%"),
                department == "writing",
                role.like("%writer%"),
                role.like("%screenplay%"),
                role.like("%story%"),
                department == "production",
                role.like("%produc%"),
                department == "editing",
                role.like("%editor%"),
                department.in_(("camera", "cinematography")),
                role.like("%cinematograph%"),
                role.like("%director of photography%"),
                department.in_(("sound", "music")),
                role.like("%composer%"),
                role.like("%music%"),
            ),
        ),
    )
    stats = (
        db.query(
            MovieCredit.person_id.label("person_id"),
            func.count(func.distinct(MovieCredit.movie_id)).label("public_credits"),
            func.max(case((significant_credit, 1), else_=0)).label("significant_credit"),
        )
        .join(Movie, Movie.id == MovieCredit.movie_id)
        .filter(Movie.safety_public_visibility == "FULL")
        .group_by(MovieCredit.person_id)
        .subquery()
    )
    identity_signal = or_(
        and_(Person.profile_path.is_not(None), func.length(func.trim(Person.profile_path)) > 0),
        and_(Person.biography.is_not(None), func.length(func.trim(Person.biography)) > 0),
        and_(Person.imdb_id.is_not(None), func.length(func.trim(Person.imdb_id)) > 0),
        Person.birthday.is_not(None),
        and_(Person.place_of_birth.is_not(None), func.length(func.trim(Person.place_of_birth)) > 0),
    )
    return db.query(Person).join(stats, stats.c.person_id == Person.id).filter(
        or_(
            stats.c.public_credits >= 2,
            and_(
                stats.c.public_credits == 1,
                or_(identity_signal, stats.c.significant_credit == 1),
            ),
        )
    )


def person_is_indexable(db, person_id: int) -> bool:
    from app.models.movie_metadata import Person

    return indexable_people_query(db).filter(Person.id == person_id).first() is not None


def robots_txt(site_url: str) -> str:
    """Origin robots.txt. Legitimate crawlers may access all public content.

    Public documents remain crawlable while internal and duplicate route
    families are explicitly excluded.
    """
    base = site_url.rstrip("/")
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /api/v1/maintenance\n"
        "Allow: /api/v1/home\n"
        "Allow: /api/v1/discover\n"
        "Allow: /api/v1/languages\n"
        "Allow: /api/v1/search\n"
        "Allow: /api/v1/ott\n"
        "Allow: /api/v1/people/\n"
        "Allow: /api/v1/calendar/\n"
        "Allow: /api/v1/movies/*/detail\n"
        "Allow: /api/v1/movies/*/public-comments\n"
        "Disallow: /api/\n"
        "Disallow: /admin/\n"
        "Disallow: /deep-search/\n"
        "Disallow: /login\n"
        "Disallow: /signup\n"
        "Disallow: /verify-email\n"
        "Disallow: /forgot-password\n"
        "Disallow: /reset-password\n"
        "Disallow: /request-movie\n"
        "Disallow: /support\n"
        "Disallow: /contributions\n"
        "Disallow: /paynow\n"
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


def sitemap_row(loc: str) -> str:
    return f"<sitemap><loc>{escape(loc)}</loc></sitemap>"


def static_urls(site_url: str, db) -> list[str]:
    """Public landing and filter-level pages that are intentionally indexable."""
    base = site_url.rstrip("/")
    from app.models.genre import Genre
    from app.models.ott_availability import OttAvailability
    from app.models.movie import Movie
    from app.services.languages import ALL_PUBLIC_LANGUAGE_CODES
    from app.services.ott_providers import PUBLIC_OTT_STATES, platform_slug

    paths = [
        "/",
        "/discover",
        "/search",
        "/ott",
        "/about",
        "/contact",
        "/privacy",
        "/terms",
        "/cookies",
        "/methodology",
        "/editorial-policy",
        "/corrections",
        "/sources",
        "/updates",
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
    populated_languages = {
        code
        for (code,) in db.query(Movie.original_language)
        .filter(
            Movie.original_language.in_(ALL_PUBLIC_LANGUAGE_CODES),
            Movie.safety_public_visibility == "FULL",
        )
        .distinct()
    }
    paths += [
        f"/languages/{code}"
        for code in ALL_PUBLIC_LANGUAGE_CODES
        if code in populated_languages
    ]
    paths += [
        f"/genres/{quote(row.slug, safe='-')}"
        for row in db.query(Genre.slug)
        .filter(Genre.movies.any(Movie.safety_public_visibility == "FULL"))
        .order_by(Genre.slug)
    ]
    paths += [
        f"/ott/{quote(slug, safe='-+')}"
        for row in db.query(OttAvailability.provider)
        .join(Movie, Movie.id == OttAvailability.movie_id)
        .filter(
            Movie.safety_public_visibility == "FULL",
            OttAvailability.country == "IN",
            OttAvailability.status.in_(PUBLIC_OTT_STATES),
        )
        .distinct()
        .order_by(OttAvailability.provider)
        if (slug := platform_slug(row[0]))
    ]
    return list(dict.fromkeys(f"{base}{path}" for path in paths))


def sitemap_file_count(url_count: int) -> int:
    return ceil(max(0, url_count) / SITEMAP_URLS_PER_FILE)


def url_row(loc: str, lastmod: str | None = None) -> str:
    tail = f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else ""
    return f"<url><loc>{escape(loc)}</loc>{tail}</url>"
