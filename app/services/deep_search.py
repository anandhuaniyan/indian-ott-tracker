"""Live, read-only TMDB lookup used by the public Deep Search experience."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import redis
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import Person
from app.services.roles import normalize_role
from app.services.languages import language_name
from app.services.tmdb.client import TMDbClient


RELEASE_TYPES = {
    1: "Premiere",
    2: "Limited theatrical",
    3: "Theatrical",
    4: "Digital",
    5: "Physical",
    6: "TV",
}


class DeepSearchService:
    """Fetch TMDB data, cache provider responses, then annotate local matches."""

    SEARCH_TTL = 600
    DETAIL_TTL = 2700

    def __init__(
        self,
        db: Session,
        *,
        client: TMDbClient | None = None,
        cache: Any | None = None,
    ):
        self.db = db
        self.client = client or TMDbClient(request_delay=0.1)
        self.cache = cache
        if cache is None:
            try:
                self.cache = redis.from_url(
                    settings.REDIS_URL,
                    socket_connect_timeout=0.2,
                    socket_timeout=0.2,
                )
            except Exception:
                self.cache = None

    @staticmethod
    def configured() -> bool:
        return bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN)

    @staticmethod
    def _cache_key(endpoint: str, params: dict[str, Any]) -> str:
        signature = json.dumps(
            [endpoint, sorted(params.items())],
            separators=(",", ":"),
            default=str,
        )
        return "deep-search:v1:" + hashlib.sha256(signature.encode()).hexdigest()

    def _cached(
        self,
        endpoint: str,
        params: dict[str, Any],
        ttl: int,
        loader: Callable[[], dict],
    ) -> dict:
        key = self._cache_key(endpoint, params)
        if self.cache is not None:
            try:
                cached = self.cache.get(key)
                if cached:
                    if isinstance(cached, bytes):
                        cached = cached.decode("utf-8")
                    return json.loads(cached)
            except Exception:
                pass
        payload = loader()
        if self.cache is not None:
            try:
                self.cache.setex(key, ttl, json.dumps(payload, separators=(",", ":")))
            except Exception:
                pass
        return payload

    def _get(self, endpoint: str, *, ttl: int, **params: Any) -> dict:
        if not self.configured():
            raise RuntimeError("TMDB access is not configured")
        cleaned = {key: value for key, value in params.items() if value not in (None, "")}
        return self._cached(
            endpoint,
            cleaned,
            ttl,
            lambda: self.client.get(endpoint, **cleaned),
        )

    @staticmethod
    def _movie_summary(item: dict, local_id: int | None = None) -> dict:
        return {
            "id": item.get("id"),
            "title": item.get("title") or item.get("original_title") or "Untitled",
            "original_title": item.get("original_title"),
            "release_date": item.get("release_date") or None,
            "original_language": item.get("original_language") or None,
            "original_language_name": language_name(item.get("original_language")),
            "overview": item.get("overview") or None,
            "poster_path": item.get("poster_path") or None,
            "backdrop_path": item.get("backdrop_path") or None,
            "popularity": item.get("popularity"),
            "vote_average": item.get("vote_average"),
            "vote_count": item.get("vote_count"),
            "local_movie_id": local_id,
            "in_library": local_id is not None,
        }

    @staticmethod
    def _person_summary(item: dict, local_id: int | None = None) -> dict:
        known_for = []
        for work in item.get("known_for") or []:
            if work.get("media_type") not in (None, "movie") or not (work.get("title") or work.get("original_title")):
                continue
            known_for.append(
                {
                    "id": work.get("id"),
                    "title": work.get("title") or work.get("original_title"),
                    "release_date": work.get("release_date") or None,
                    "poster_path": work.get("poster_path") or None,
                }
            )
        return {
            "id": item.get("id"),
            "name": item.get("name") or "Unknown",
            "profile_path": item.get("profile_path") or None,
            "known_for_department": item.get("known_for_department") or None,
            "popularity": item.get("popularity"),
            "known_for": known_for,
            "local_person_id": local_id,
            "in_library": local_id is not None,
        }

    def _movie_matches(self, items: list[dict]) -> dict[int, int]:
        ids = {item.get("id") for item in items if item.get("id")}
        if not ids:
            return {}
        return {
            tmdb_id: local_id
            for tmdb_id, local_id in self.db.query(Movie.tmdb_id, Movie.id)
            .filter(Movie.tmdb_id.in_(ids))
            .all()
        }

    def _person_matches(self, items: list[dict]) -> dict[int, int]:
        ids = {item.get("id") for item in items if item.get("id")}
        if not ids:
            return {}
        return {
            tmdb_id: local_id
            for tmdb_id, local_id in self.db.query(Person.tmdb_id, Person.id)
            .filter(Person.tmdb_id.in_(ids))
            .all()
        }

    def search_movies(
        self,
        query: str,
        *,
        year: int | None = None,
        language: str | None = None,
        page: int = 1,
    ) -> dict:
        params = {
            "query": query,
            "page": page,
            "include_adult": False,
            "year": year,
        }
        payload = self._get("/search/movie", ttl=self.SEARCH_TTL, **params)
        raw = payload.get("results") or []
        # TMDB's search ``language`` parameter localizes returned text; it does
        # not filter original language. Keep the lookup a genuine movie search
        # and apply the requested original-language filter to its live results.
        if language:
            raw = [item for item in raw if item.get("original_language") == language]
        matches = self._movie_matches(raw)
        return {
            "source": "live",
            "page": payload.get("page", page),
            "total_pages": min(payload.get("total_pages") or 0, 500),
            "total_results": len(raw) if language else payload.get("total_results") or 0,
            "results": [self._movie_summary(item, matches.get(item.get("id"))) for item in raw],
        }

    def search_people(self, query: str, *, page: int = 1) -> dict:
        payload = self._get(
            "/search/person",
            ttl=self.SEARCH_TTL,
            query=query,
            page=page,
            include_adult=False,
        )
        raw = payload.get("results") or []
        matches = self._person_matches(raw)
        return {
            "source": "live",
            "page": payload.get("page", page),
            "total_pages": min(payload.get("total_pages") or 0, 500),
            "total_results": payload.get("total_results") or 0,
            "results": [self._person_summary(item, matches.get(item.get("id"))) for item in raw],
        }

    def find_imdb(self, external_id: str) -> dict:
        payload = self._get(
            f"/find/{external_id}",
            ttl=self.SEARCH_TTL,
            external_source="imdb_id",
        )
        movie_raw = payload.get("movie_results") or []
        person_raw = payload.get("person_results") or []
        movie_matches = self._movie_matches(movie_raw)
        person_matches = self._person_matches(person_raw)
        return {
            "source": "live",
            "external_id": external_id,
            "movies": [self._movie_summary(item, movie_matches.get(item.get("id"))) for item in movie_raw],
            "people": [self._person_summary(item, person_matches.get(item.get("id"))) for item in person_raw],
        }

    @staticmethod
    def _credit(item: dict, *, cast: bool = False) -> dict:
        return {
            "id": item.get("id"),
            "name": item.get("name") or "Unknown",
            "profile_path": item.get("profile_path") or None,
            "character": item.get("character") if cast else None,
            "job": item.get("job") if not cast else None,
            "department": item.get("department") if not cast else "Acting",
            "order": item.get("order") if cast else None,
        }

    @staticmethod
    def _crew_group(item: dict) -> str:
        role = normalize_role(item.get("job"))
        return {
            "director": "Directing",
            "writer": "Writing",
            "cinematography": "Cinematography",
            "producer": "Production",
            "editor": "Editing",
            "composer": "Music",
        }.get(role, "Other Crew")

    @staticmethod
    def _images(payload: dict) -> dict:
        def values(name: str, maximum: int) -> list[dict]:
            return [
                {
                    "file_path": item.get("file_path"),
                    "language": item.get("iso_639_1"),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "aspect_ratio": item.get("aspect_ratio"),
                    "vote_average": item.get("vote_average"),
                }
                for item in (payload.get(name) or [])[:maximum]
                if item.get("file_path")
            ]
        return {
            "posters": values("posters", 30),
            "backdrops": values("backdrops", 30),
            "logos": values("logos", 20),
        }

    def movie_detail(self, movie_id: int) -> dict:
        append = "credits,release_dates,external_ids,keywords,alternative_titles,recommendations,similar,watch/providers"
        payload = self._get(
            f"/movie/{movie_id}",
            ttl=self.DETAIL_TTL,
            append_to_response=append,
        )
        original_language = payload.get("original_language") or "en"
        image_languages = ",".join(dict.fromkeys(["en", original_language, "null"]))
        image_payload = self._get(
            f"/movie/{movie_id}/images",
            ttl=self.DETAIL_TTL,
            include_image_language=image_languages,
        )
        local = self.db.query(Movie).filter_by(tmdb_id=movie_id).first()
        credits = payload.get("credits") or {}
        crew_groups: dict[str, list[dict]] = {}
        for item in credits.get("crew") or []:
            crew_groups.setdefault(self._crew_group(item), []).append(self._credit(item))
        release_countries = []
        for country in (payload.get("release_dates") or {}).get("results") or []:
            releases = [
                {
                    "date": (item.get("release_date") or "")[:10] or None,
                    "type": RELEASE_TYPES.get(item.get("type"), "Other"),
                    "certification": item.get("certification") or None,
                    "note": item.get("note") or None,
                }
                for item in country.get("release_dates") or []
                if item.get("release_date")
            ]
            if releases:
                release_countries.append({"country": country.get("iso_3166_1"), "releases": releases})
        release_countries.sort(key=lambda item: (item["country"] != "IN", item["country"] or ""))
        keywords = (payload.get("keywords") or {}).get("keywords") or []
        titles = (payload.get("alternative_titles") or {}).get("titles") or []
        india = ((payload.get("watch/providers") or {}).get("results") or {}).get("IN") or {}
        providers = []
        for provider_type in ("flatrate", "free", "ads", "rent", "buy"):
            for item in india.get(provider_type) or []:
                providers.append(
                    {
                        "id": item.get("provider_id"),
                        "name": item.get("provider_name"),
                        "logo_path": item.get("logo_path"),
                        "type": provider_type,
                    }
                )
        return {
            "source": "live",
            "movie": self._movie_summary(payload, local.id if local else None)
            | {
                "tagline": payload.get("tagline") or None,
                "status": payload.get("status") or None,
                "runtime": payload.get("runtime"),
                "genres": payload.get("genres") or [],
                "spoken_languages": [
                    item | {"english_name": language_name(item.get("iso_639_1"), item.get("english_name") or item.get("name"))}
                    for item in payload.get("spoken_languages") or []
                ],
                "production_countries": payload.get("production_countries") or [],
                "production_companies": payload.get("production_companies") or [],
                "budget": payload.get("budget"),
                "revenue": payload.get("revenue"),
                "collection": payload.get("belongs_to_collection"),
                "homepage": payload.get("homepage") or None,
            },
            "cast": [self._credit(item, cast=True) for item in (credits.get("cast") or [])],
            "crew": crew_groups,
            "images": self._images(image_payload),
            "releases": release_countries,
            "external_ids": {
                key: value
                for key, value in (payload.get("external_ids") or {}).items()
                if value and key in {"imdb_id", "wikidata_id", "facebook_id", "instagram_id", "twitter_id"}
            },
            "keywords": [{"id": item.get("id"), "name": item.get("name")} for item in keywords if item.get("name")],
            "alternative_titles": [
                {"title": item.get("title"), "country": item.get("iso_3166_1"), "type": item.get("type") or None}
                for item in titles
                if item.get("title")
            ],
            "recommendations": [self._movie_summary(item) for item in ((payload.get("recommendations") or {}).get("results") or [])],
            "similar": [self._movie_summary(item) for item in ((payload.get("similar") or {}).get("results") or [])],
            "watch_providers": {"country": "IN", "link": india.get("link"), "items": providers},
        }

    def person_detail(self, person_id: int) -> dict:
        payload = self._get(
            f"/person/{person_id}",
            ttl=self.DETAIL_TTL,
            append_to_response="movie_credits,external_ids,images",
        )
        local = self.db.query(Person).filter_by(tmdb_id=person_id).first()
        credits = payload.get("movie_credits") or {}
        groups: dict[str, list[dict]] = {
            "Acting": [], "Directing": [], "Writing": [], "Cinematography": [],
            "Production": [], "Music": [], "Editing": [], "Other Crew": [],
        }
        for item in credits.get("cast") or []:
            groups["Acting"].append(
                self._movie_summary(item) | {"character": item.get("character") or None, "job": None}
            )
        for item in credits.get("crew") or []:
            groups[self._crew_group(item)].append(
                self._movie_summary(item) | {"character": None, "job": item.get("job") or None}
            )
        for items in groups.values():
            items.sort(key=lambda item: item.get("release_date") or "", reverse=True)
        return {
            "source": "live",
            "person": {
                "id": payload.get("id"),
                "name": payload.get("name") or "Unknown",
                "profile_path": payload.get("profile_path") or None,
                "biography": payload.get("biography") or None,
                "birthday": payload.get("birthday") or None,
                "deathday": payload.get("deathday") or None,
                "place_of_birth": payload.get("place_of_birth") or None,
                "known_for_department": payload.get("known_for_department") or None,
                "homepage": payload.get("homepage") or None,
                "popularity": payload.get("popularity"),
                "local_person_id": local.id if local else None,
                "in_library": local is not None,
            },
            "external_ids": {
                key: value
                for key, value in (payload.get("external_ids") or {}).items()
                if value and key in {"imdb_id", "wikidata_id", "facebook_id", "instagram_id", "twitter_id"}
            },
            "profiles": self._images({"posters": (payload.get("images") or {}).get("profiles") or []})["posters"],
            "credits": {key: value for key, value in groups.items() if value},
        }
