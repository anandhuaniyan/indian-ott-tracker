from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import json
import re
import urllib.parse
from datetime import date, datetime

from app.database.connection import get_db
from app.config.settings import settings

router = APIRouter()

SITE_URL = settings.SITE_URL.rstrip("/")
SITE_NAME = "OTT Tracker"

BOT_UA_PATTERN = re.compile(
    r"(googlebot|bingbot|slurp|duckduckbot|baiduspider|yandexbot|sogou|pinterestbot|facebookexternalhit|twitterbot|linkedinbot|whatsapp|telegrambot|applebot|amazonbot|mj12bot|ahrefsbot|semrushbot|bytespider|kirimai|youindex|cocrawler)",
    re.IGNORECASE,
)


class _JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


def _safe_json(obj: dict | list) -> str:
    value = json.dumps(obj, cls=_JSONEncoder)
    return (
        value
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _meta(name: str, content: str) -> str:
    escaped = (
        content.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    if name.startswith("og:") or name.startswith("twitter:"):
        return f'<meta property="{name}" content="{escaped}">'
    return f'<meta name="{name}" content="{escaped}">'


def _render_html(
    title: str,
    description: str,
    image: str | None,
    canonical_path: str,
    json_ld: list[dict] | dict | None,
    noindex: bool = False,
    bot_detected: bool = False,
) -> str:
    full_title = (
        f"{title} | {SITE_NAME}"
        if title and title != "OTT Tracker"
        else f"{SITE_NAME} - Movies & OTT Releases Worldwide"
    )
    canonical = f"{SITE_URL}{canonical_path}"

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{full_title}</title>")
    parts.append(_meta("description", description))
    parts.append(_meta("og:title", full_title))
    parts.append(_meta("og:description", description))
    parts.append(_meta("og:type", "website"))
    parts.append(_meta("og:url", canonical))
    parts.append(_meta("og:site_name", SITE_NAME))
    parts.append(_meta("og:locale", "en_IN"))
    parts.append(_meta("twitter:card", "summary_large_image" if image else "summary"))
    parts.append(_meta("twitter:title", full_title))
    parts.append(_meta("twitter:description", description))

    robots_value = "noindex,follow" if (noindex or not bot_detected) else "index,follow"
    parts.append(_meta("robots", robots_value))

    if image:
        parts.append(_meta("og:image", image))
        parts.append(_meta("twitter:image", image))

    parts.append(f'<link rel="canonical" href="{canonical}">')

    if json_ld:
        entries = [json_ld] if isinstance(json_ld, dict) else json_ld
        for entry in entries:
            if entry:
                parts.append(
                    f"<script type=\"application/ld+json\">{_safe_json(entry)}</script>"
                )

    parts.append("</head>")
    parts.append("<body>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def _build_breadcrumb_json_ld(crumbs: list[dict]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": crumb["name"],
                "item": f"{SITE_URL}{crumb['path']}",
            }
            for i, crumb in enumerate(crumbs)
        ],
    }


def _img_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"https://image.tmdb.org/t/p/original{path}"


async def _extract_path(request: Request) -> str:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        body = await request.body()
        parsed = urllib.parse.parse_qs(body.decode("utf-8"))
        paths = parsed.get("path", [])
        if paths:
            return paths[0]
    elif "application/json" in content_type:
        body = await request.body()
        try:
            data = json.loads(body)
            return data.get("path", "")
        except (json.JSONDecodeError, TypeError):
            pass
    return ""


@router.api_route("/_render", response_class=HTMLResponse, methods=["GET", "POST"])
async def render_for_crawler(
    request: Request,
    db: Session = Depends(get_db),
):
    from app.api.v1.public import (
        movie_detail,
        person_detail,
    )
    from app.seo import person_is_indexable
    from app.services.languages import language_name

    path = await _extract_path(request)
    user_agent = request.headers.get("user-agent", "")
    bot_detected = bool(BOT_UA_PATTERN.search(user_agent))

    if not path:
        raise HTTPException(400, "path is required")

    path = path.strip()
    canonical_path = path
    parts = path.strip("/").split("/")
    parts = [p for p in parts if p]

    # --- Movie detail: /movies/{id} ---
    if len(parts) >= 2 and parts[0] == "movies":
        movie_id_str = parts[1]
        try:
            movie_id = int(re.sub(r"[^0-9]", "", movie_id_str))
        except ValueError:
            raise HTTPException(404, "Invalid movie ID")

        try:
            detail = movie_detail(movie_id, db=db)
        except HTTPException:
            raise

        movie_data = detail["movie"]
        description = movie_data.get("overview") or (
            f"Details and OTT availability for {movie_data['title']}."
        )
        image_url = _img_url(movie_data.get("backdrop_path") or movie_data.get("poster_path"))

        movie_ld = {
            "@context": "https://schema.org",
            "@type": "Movie",
            "name": movie_data["title"],
        }
        if movie_data.get("original_title"):
            movie_ld["alternateName"] = movie_data["original_title"]
        if movie_data.get("overview"):
            movie_ld["description"] = movie_data["overview"]
        if movie_data.get("release_date"):
            movie_ld["dateCreated"] = movie_data["release_date"]
        if movie_data.get("poster_path"):
            movie_ld["image"] = _img_url(movie_data["poster_path"])
        if movie_data.get("runtime_minutes"):
            movie_ld["duration"] = f"PT{movie_data['runtime_minutes']}M"
        if movie_data.get("rating") is not None:
            movie_ld["aggregateRating"] = {
                "@type": "AggregateRating",
                "ratingValue": movie_data["rating"],
                "ratingCount": movie_data.get("vote_count") or 0,
                "bestRating": 10,
                "author": {"@type": "Organization", "name": "IMDb"},
            }

        cast = detail.get("cast", [])
        directors = detail.get("crew_by_role", {}).get("director", [])
        if cast:
            movie_ld["actor"] = [
                {"@type": "Person", "name": item["name"]} for item in cast[:10]
            ]
        if directors:
            movie_ld["director"] = [
                {"@type": "Person", "name": item["name"]} for item in directors
            ]

        trailer = detail.get("trailer")
        if trailer and trailer.get("video_key"):
            movie_ld["video"] = {
                "@type": "VideoObject",
                "name": trailer.get("name") or f"{movie_data['title']} Trailer",
                "description": movie_data.get("overview") or f"Official trailer for {movie_data['title']}.",
                "uploadDate": trailer.get("published_at", "").strftime("%Y-%m-%d")
                if trailer.get("published_at")
                else None,
                "thumbnailUrl": f"https://i.ytimg.com/vi/{trailer['video_key']}/hqdefault.jpg",
                "embedUrl": trailer.get("embed_url"),
            }

        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": "Movies", "path": "/discover"},
            {"name": movie_data["title"], "path": canonical_path},
        ])

        return _render_html(
            title=movie_data["title"],
            description=description,
            image=image_url,
            canonical_path=canonical_path,
            json_ld=[movie_ld, breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    # --- Person detail: /people/{id} ---
    if len(parts) >= 2 and parts[0] == "people":
        person_id_str = parts[1]
        try:
            person_id = int(re.sub(r"[^0-9]", "", person_id_str))
        except ValueError:
            raise HTTPException(404, "Invalid person ID")

        try:
            detail = person_detail(person_id, db=db)
        except HTTPException:
            raise

        indexable = person_is_indexable(db, person_id)
        image_url = _img_url(detail.get("profile_path"))
        description = f"{detail['name']} filmography and movie credits."

        person_ld = {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": detail["name"],
        }
        if detail.get("profile_path"):
            person_ld["image"] = image_url
        if detail.get("department"):
            person_ld["jobTitle"] = detail["department"]
        if detail.get("birthday"):
            person_ld["birthDate"] = detail["birthday"]
        if detail.get("place_of_birth"):
            person_ld["birthPlace"] = detail["place_of_birth"]
        if detail.get("imdb_url"):
            person_ld["sameAs"] = [detail["imdb_url"]]

        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": detail["name"], "path": canonical_path},
        ])

        return _render_html(
            title=detail["name"],
            description=description,
            image=image_url,
            canonical_path=canonical_path,
            json_ld=[person_ld, breadcrumb],
            noindex=not indexable,
            bot_detected=bot_detected,
        )

    # --- Genre browse: /genres/{slug} ---
    if len(parts) >= 2 and parts[0] == "genres":
        slug = parts[1]
        display_name = slug.replace("-", " ").title()
        description = f"Browse {display_name} movies, release information and verified India OTT availability."
        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": f"{display_name} movies", "path": canonical_path},
        ])
        return _render_html(
            title=f"{display_name} movies",
            description=description,
            image=None,
            canonical_path=canonical_path,
            json_ld=[breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    # --- Language browse: /languages/{code} ---
    if len(parts) >= 2 and parts[0] == "languages":
        code = parts[1]
        display_name = language_name(code) or code
        description = f"Browse {display_name} movies, release information and verified India OTT availability."
        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": f"{display_name} movies", "path": canonical_path},
        ])
        return _render_html(
            title=f"{display_name} movies",
            description=description,
            image=None,
            canonical_path=canonical_path,
            json_ld=[breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    # --- OTT platform: /ott/{platform} ---
    if len(parts) >= 2 and parts[0] == "ott":
        platform_slug = parts[1]
        display_name = platform_slug.replace("-", " ").title()
        description = f"Browse {display_name} movies and OTT releases available across global and regional streaming markets."
        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": "OTT", "path": "/ott"},
            {"name": display_name, "path": canonical_path},
        ])
        return _render_html(
            title=f"{display_name} movies",
            description=description,
            image=None,
            canonical_path=canonical_path,
            json_ld=[breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    # --- Calendar: /calendar/{period} ---
    if len(parts) >= 2 and parts[0] == "calendar":
        period = parts[1]
        display_name = period.replace("-", " ")
        description = f"Browse {display_name} movie and OTT releases worldwide by date and language."
        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": "Release calendar", "path": "/calendar/this-week"},
            {"name": display_name, "path": canonical_path},
        ])
        return _render_html(
            title=f"Releases {display_name}",
            description=description,
            image=None,
            canonical_path=canonical_path,
            json_ld=[breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    # --- Top-level pages ---
    if len(parts) == 0:
        # Root path "/" — homepage
        website_ld = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "OTT Tracker",
            "description": "Discover movies from India and around the world across theaters and streaming platforms.",
            "url": SITE_URL,
            "potentialAction": {
                "@type": "SearchAction",
                "target": f"{SITE_URL}/search?q={{search_term_string}}",
                "query-input": "required name=search_term_string",
            },
        }
        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
        ])
        return _render_html(
            title="OTT Tracker",
            description="OTT Tracker — Discover popular, upcoming and recently released movies across theaters and streaming platforms worldwide, with release dates, ratings, trailers and verified OTT availability.",
            image=None,
            canonical_path="/",
            json_ld=[website_ld, breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    if len(parts) == 1 and parts[0] in ("discover", "search", "ott", "calendar"):
        route_map = {
            "discover": ("Discover", "Discover movies and OTT releases worldwide."),
            "search": ("Search", "Search movies, people, and OTT releases."),
            "ott": ("OTT releases", "Confirmed and currently available streaming information from canonical records."),
            "calendar": ("Release calendar", "Movie release calendar with theatrical and OTT dates."),
        }
        route_name, description = route_map[parts[0]]
        breadcrumb = _build_breadcrumb_json_ld([
            {"name": "Home", "path": "/"},
            {"name": route_name, "path": canonical_path},
        ])
        return _render_html(
            title=route_name,
            description=description,
            image=None,
            canonical_path=canonical_path,
            json_ld=[breadcrumb],
            noindex=False,
            bot_detected=bot_detected,
        )

    raise HTTPException(404, "Path not supported for rendering")
