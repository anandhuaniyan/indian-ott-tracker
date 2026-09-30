"""Safe TMDB enrichment for existing movie records only."""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.movie import Movie
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie_metadata import (
    AlternativeTitle,
    ExternalId,
    Keyword,
    MovieCredit,
    MovieImage,
    MovieKeyword,
    MovieProductionCompany,
    MovieProductionCountry,
    MovieRating,
    MovieReleaseDate,
    Person,
    ProductionCompany,
    ProductionCountry,
)
from app.models.ott_availability import OttAvailability
from app.services.tmdb.movie_service import TMDbMovieService
from app.services.artwork_service import ArtworkService
from app.services.trailers import TrailerService
from app.services.ott_providers import normalize_platform
from app.services.languages import normalize_language_code


class MovieMetadataService:
    """Enriches an existing movie without changing its identity or deleting valid data."""

    def __init__(
        self,
        db: Session,
        *,
        cache_artwork: bool = True,
        include_ott: bool = True,
    ):
        self.db = db
        self.tmdb = TMDbMovieService()
        self.artwork = ArtworkService()
        self.cache_artwork = cache_artwork
        self.include_ott = include_ott

    def enrich_movie(self, movie: Movie, payload: dict | None = None) -> Movie:
        """Enrich a movie, reusing a verified TMDB payload when supplied."""
        payload = payload or self.tmdb.get_rich_movie_details(movie.tmdb_id)
        self._update_movie_scalars(movie, payload)
        self._upsert_genres_and_languages(movie, payload)
        self._upsert_external_ids(movie, payload.get("external_ids", {}))
        self._upsert_alternative_titles(movie, payload.get("alternative_titles", {}))
        self._upsert_credits(movie, payload.get("credits", {}))
        self._upsert_keywords(movie, payload.get("keywords", {}))
        self._upsert_production(movie, payload)
        self._upsert_releases(movie, payload.get("release_dates", {}))
        self._upsert_images(movie, payload.get("images", {}))
        if self.include_ott:
            self._upsert_watch_providers(movie, payload.get("watch/providers", {}))
        TrailerService(self.db).upsert(movie, payload.get("videos", {}))
        self.db.flush()
        expected_cast = bool(payload.get("credits", {}).get("cast"))
        expected_crew = bool(payload.get("credits", {}).get("crew"))
        if (
            expected_cast
            and not self.db.query(MovieCredit.id)
            .filter_by(movie_id=movie.id, credit_type="cast")
            .first()
        ):
            raise RuntimeError(
                f"TMDB returned cast for movie {movie.id}, but no cast credit was saved"
            )
        if (
            expected_crew
            and not self.db.query(MovieCredit.id)
            .filter_by(movie_id=movie.id, credit_type="crew")
            .first()
        ):
            raise RuntimeError(
                f"TMDB returned crew for movie {movie.id}, but no crew credit was saved"
            )
        if self.cache_artwork:
            for image in (
                self.db.query(MovieImage)
                .filter_by(movie_id=movie.id, is_primary=True)
                .all()
            ):
                self.artwork.cache(image)
        self._upsert_tmdb_rating(movie, payload)
        self.db.commit()
        self.db.refresh(movie)
        return movie

    @staticmethod
    def _set_if_present(instance: object, field: str, value: object) -> None:
        if value not in (None, "", [], {}):
            setattr(instance, field, value)

    def _update_movie_scalars(self, movie: Movie, payload: dict) -> None:
        for field in (
            "title",
            "original_title",
            "overview",
            "poster_path",
            "backdrop_path",
        ):
            self._set_if_present(movie, field, payload.get(field))
        self._set_if_present(
            movie,
            "original_language",
            normalize_language_code(payload.get("original_language")),
        )
        if payload.get("release_date"):
            try:
                movie.release_date = date.fromisoformat(payload["release_date"][:10])
            except (TypeError, ValueError):
                pass
        for field in (
            "tagline",
            "budget",
            "revenue",
            "status",
            "runtime",
            "popularity",
            "vote_average",
            "vote_count",
        ):
            target = "runtime_minutes" if field == "runtime" else field
            self._set_if_present(movie, target, payload.get(field))
        collection = payload.get("belongs_to_collection") or {}
        if collection:
            self._set_if_present(movie, "collection_tmdb_id", collection.get("id"))
            self._set_if_present(movie, "collection_name", collection.get("name"))
            self._set_if_present(
                movie, "collection_poster_path", collection.get("poster_path")
            )
            self._set_if_present(
                movie, "collection_backdrop_path", collection.get("backdrop_path")
            )

    def _upsert_genres_and_languages(self, movie: Movie, payload: dict) -> None:
        from slugify import slugify

        for item in payload.get("genres", []):
            if not item.get("id") or not item.get("name"):
                continue
            genre = self.db.query(Genre).filter_by(tmdb_id=item["id"]).first()
            if not genre:
                base_slug = slugify(item["name"])
                slug = (
                    base_slug
                    if not self.db.query(Genre.id).filter_by(slug=base_slug).first()
                    else f"{base_slug}-{item['id']}"
                )
                genre = Genre(tmdb_id=item["id"], name=item["name"], slug=slug)
                self.db.add(genre)
                self.db.flush()
            if genre not in movie.genres:
                movie.genres.append(genre)
        for item in payload.get("spoken_languages", []):
            code = item.get("iso_639_1")
            if not code:
                continue
            language = self.db.query(Language).filter_by(iso_639_1=code).first()
            if not language:
                language = Language(
                    iso_639_1=code,
                    english_name=item.get("english_name") or item.get("name") or code,
                    native_name=item.get("name") or None,
                )
                self.db.add(language)
                self.db.flush()
            if language not in movie.languages:
                movie.languages.append(language)

    def _upsert_alternative_titles(self, movie: Movie, data: dict) -> None:
        staged: set[tuple[str | None, str]] = set()
        for item in data.get("titles", []):
            title = (item.get("title") or "").strip()[:500]
            if not title:
                continue
            country = (item.get("iso_3166_1") or "").strip()[:2] or None
            key = (country, title)
            if key in staged:
                continue
            staged.add(key)
            record = (
                self.db.query(AlternativeTitle)
                .filter_by(movie_id=movie.id, country=country, title=title)
                .first()
            )
            if not record:
                self.db.add(
                    AlternativeTitle(
                        movie_id=movie.id,
                        country=country,
                        title=title,
                        title_type=(item.get("type") or "").strip()[:100] or None,
                    )
                )

    def _upsert_external_ids(self, movie: Movie, data: dict) -> None:
        values = {
            "imdb": data.get("imdb_id"),
            "wikidata": data.get("wikidata_id"),
            "facebook": data.get("facebook_id"),
            "instagram": data.get("instagram_id"),
            "twitter": data.get("twitter_id"),
        }
        for provider, external_id in values.items():
            if not external_id:
                continue
            record = (
                self.db.query(ExternalId)
                .filter_by(movie_id=movie.id, provider=provider)
                .first()
            )
            if record:
                # Do not steal an identity already attached to another movie. Shared
                # social accounts are common for studios and production campaigns.
                duplicate = (
                    self.db.query(ExternalId)
                    .filter(
                        ExternalId.provider == provider,
                        ExternalId.external_id == external_id,
                        ExternalId.movie_id != movie.id,
                    )
                    .first()
                )
                if not duplicate:
                    record.external_id = external_id
            else:
                duplicate = (
                    self.db.query(ExternalId)
                    .filter_by(provider=provider, external_id=external_id)
                    .first()
                )
                if not duplicate:
                    self.db.add(
                        ExternalId(
                            movie_id=movie.id,
                            provider=provider,
                            external_id=external_id,
                        )
                    )
            if (
                provider == "imdb"
                and self.db.query(ExternalId.id)
                .filter_by(movie_id=movie.id, provider="imdb")
                .first()
            ):
                # A newly imported or recovered IMDb ID is immediately visible
                # to the bounded rating refresh job; no global backfill restart
                # is required.
                from app.services.rating_provider import ensure_pending_rating

                ensure_pending_rating(self.db, movie.id)

    def _person(self, item: dict) -> Person:
        person = self.db.query(Person).filter_by(tmdb_id=item["id"]).first()
        if not person:
            try:
                # Parallel language imports can encounter the same contributor.
                # A savepoint lets the losing transaction reuse the canonical
                # person without rolling back the movie being enriched.
                with self.db.begin_nested():
                    person = Person(
                        tmdb_id=item["id"], name=item.get("name") or "Unknown"
                    )
                    self.db.add(person)
                    self.db.flush()
            except IntegrityError:
                person = self.db.query(Person).filter_by(tmdb_id=item["id"]).first()
                if not person:
                    raise
        self._set_if_present(person, "name", item.get("name"))
        self._set_if_present(person, "profile_path", item.get("profile_path"))
        self._set_if_present(
            person, "known_for_department", item.get("known_for_department")
        )
        return person

    def _upsert_credits(self, movie: Movie, data: dict) -> None:
        for credit_type, items in (
            ("cast", data.get("cast", [])),
            ("crew", data.get("crew", [])),
        ):
            for item in items:
                if not item.get("id"):
                    continue
                tmdb_credit_id = item.get("credit_id")
                person = self._person(item)

                # Try to find by TMDB credit ID first
                credit = None
                if tmdb_credit_id:
                    credit = self.db.query(MovieCredit).filter_by(
                        movie_id=movie.id, tmdb_credit_id=tmdb_credit_id
                    ).first()

                # Fallback to legacy lookup
                if not credit:
                    filters = {
                        "movie_id": movie.id,
                        "person_id": person.id,
                        "credit_type": credit_type,
                        "job": item.get("job"),
                        "character": item.get("character"),
                    }
                    credit = self.db.query(MovieCredit).filter_by(**filters).first()

                # Create if not found
                if not credit:
                    credit = MovieCredit(
                        movie_id=movie.id,
                        person_id=person.id,
                        credit_type=credit_type,
                        job=item.get("job"),
                        character=item.get("character")
                    )
                    self.db.add(credit)

                # Update fields
                credit.tmdb_credit_id = tmdb_credit_id or credit.tmdb_credit_id
                credit.cast_order = (
                    item.get("order") if credit_type == "cast" else credit.cast_order
                )
                credit.department = item.get("department") or credit.department
                credit.job = item.get("job") or credit.job
                credit.character = item.get("character") or credit.character

    def _upsert_keywords(self, movie: Movie, data: dict) -> None:
        for item in data.get("keywords", []):
            if not item.get("id") or not item.get("name"):
                continue
            keyword = self.db.query(Keyword).filter_by(tmdb_id=item["id"]).first()
            if not keyword:
                keyword = Keyword(tmdb_id=item["id"], name=item["name"])
                self.db.add(keyword)
                self.db.flush()
            if (
                not self.db.query(MovieKeyword)
                .filter_by(movie_id=movie.id, keyword_id=keyword.id)
                .first()
            ):
                self.db.add(MovieKeyword(movie_id=movie.id, keyword_id=keyword.id))

    def _upsert_production(self, movie: Movie, payload: dict) -> None:
        staged_companies: set[int] = set()
        for item in payload.get("production_companies", []):
            if not item.get("id") or not item.get("name"):
                continue
            company = (
                self.db.query(ProductionCompany).filter_by(tmdb_id=item["id"]).first()
            )
            if not company:
                company = ProductionCompany(tmdb_id=item["id"], name=item["name"])
                self.db.add(company)
                self.db.flush()
            self._set_if_present(company, "logo_path", item.get("logo_path"))
            self._set_if_present(company, "origin_country", item.get("origin_country"))
            if company.id in staged_companies:
                continue
            staged_companies.add(company.id)
            if (
                not self.db.query(MovieProductionCompany)
                .filter_by(movie_id=movie.id, production_company_id=company.id)
                .first()
            ):
                self.db.add(
                    MovieProductionCompany(
                        movie_id=movie.id, production_company_id=company.id
                    )
                )
        staged_countries: set[int] = set()
        for item in payload.get("production_countries", []):
            code = item.get("iso_3166_1")
            if not code or not item.get("name"):
                continue
            country = (
                self.db.query(ProductionCountry).filter_by(iso_3166_1=code).first()
            )
            if not country:
                country = ProductionCountry(iso_3166_1=code, name=item["name"])
                self.db.add(country)
                self.db.flush()
            if country.id in staged_countries:
                continue
            staged_countries.add(country.id)
            if (
                not self.db.query(MovieProductionCountry)
                .filter_by(movie_id=movie.id, production_country_id=country.id)
                .first()
            ):
                self.db.add(
                    MovieProductionCountry(
                        movie_id=movie.id, production_country_id=country.id
                    )
                )

    def _upsert_releases(self, movie: Movie, data: dict) -> None:
        for country_data in data.get("results", []):
            country = country_data.get("iso_3166_1")
            for item in country_data.get("release_dates", []):
                value = item.get("release_date")
                if not country or not value:
                    continue
                try:
                    release_date = date.fromisoformat(value[:10])
                except ValueError:
                    continue
                release_type = str(item.get("type") or "unknown")
                record = (
                    self.db.query(MovieReleaseDate)
                    .filter_by(
                        movie_id=movie.id,
                        country=country,
                        release_date=release_date,
                        release_type=release_type,
                    )
                    .first()
                )
                if not record:
                    self.db.add(
                        MovieReleaseDate(
                            movie_id=movie.id,
                            country=country,
                            release_date=release_date,
                            release_type=release_type,
                            certification=item.get("certification") or None,
                            note=item.get("note") or None,
                        )
                    )

    def _upsert_images(self, movie: Movie, data: dict) -> None:
        staged: dict[tuple[str, str], MovieImage] = {}
        for image_type, items in (
            ("poster", data.get("posters", [])),
            ("backdrop", data.get("backdrops", [])),
            ("logo", data.get("logos", [])),
        ):
            for index, item in enumerate(items):
                path = item.get("file_path")
                if not path:
                    continue
                source_id = path
                key = (image_type, source_id)
                record = staged.get(key)
                if record is None:
                    record = (
                        self.db.query(MovieImage)
                        .filter_by(
                            movie_id=movie.id,
                            image_type=image_type,
                            source="tmdb",
                            source_id=source_id,
                        )
                        .first()
                    )
                if not record:
                    record = MovieImage(
                        movie_id=movie.id,
                        image_type=image_type,
                        source="tmdb",
                        source_id=source_id,
                    )
                    self.db.add(record)
                staged[key] = record
                record.original_url = f"https://image.tmdb.org/t/p/original{path}"
                record.language = item.get("iso_639_1") or None
                record.width = item.get("width")
                record.height = item.get("height")
                record.aspect_ratio = item.get("aspect_ratio")
                record.is_primary = bool(record.is_primary or index == 0)

    def _upsert_tmdb_rating(self, movie: Movie, payload: dict) -> None:
        record = (
            self.db.query(MovieRating)
            .filter_by(movie_id=movie.id, source="tmdb")
            .first()
        )
        if not record:
            record = MovieRating(movie_id=movie.id, source="tmdb")
            self.db.add(record)
        record.rating = payload.get("vote_average")
        record.vote_count = payload.get("vote_count")
        record.status = "AVAILABLE" if record.rating is not None else "NOT_YET_RATED"
        record.last_updated_at = datetime.now(timezone.utc)

    def _upsert_watch_providers(self, movie: Movie, data: dict) -> None:
        india = (data.get("results") or {}).get("IN") or {}
        staged: dict[tuple[str, str], OttAvailability] = {}
        watch_types = {
            "flatrate": "subscription",
            "rent": "rent",
            "buy": "buy",
            "free": "free",
            "ads": "ads",
        }
        for provider_type, watch_type in watch_types.items():
            for item in india.get(provider_type, []):
                name = normalize_platform(item.get("provider_name"))
                if not name:
                    continue
                key = (name, watch_type)
                availability = staged.get(key)
                if availability is None:
                    rows = (
                        self.db.query(OttAvailability)
                        .filter_by(movie_id=movie.id, country="IN", watch_type=watch_type)
                        .all()
                    )
                    matches = [row for row in rows if normalize_platform(row.provider) == name]
                    # Historical data can contain both an alias and its normalized
                    # name. Prefer the exact row so renaming the alias cannot hit
                    # the database uniqueness constraint.
                    availability = next((row for row in matches if row.provider == name), None)
                    availability = availability or next(
                        (row for row in matches if row.manually_verified or row.locked_by_admin),
                        None,
                    )
                    availability = availability or (matches[0] if matches else None)
                if not availability:
                    availability = OttAvailability(
                        movie_id=movie.id,
                        provider=name,
                        country="IN",
                        watch_type=watch_type,
                    )
                    self.db.add(availability)
                staged[key] = availability
                if not (availability.manually_verified or availability.locked_by_admin):
                    availability.provider = name
                availability.provider_logo = (
                    item.get("logo_path") or availability.provider_logo
                )
                if not availability.manually_verified:
                    availability.status = (
                        availability.status
                        if availability.verification_status == "CONFIRMED"
                        else "available"
                    )
                    if availability.verification_status != "CONFIRMED":
                        availability.source_type = "tmdb"
                        availability.source_url = (
                            india.get("link") or availability.source_url
                        )
                        availability.confidence = max(availability.confidence or 0, 80)
                availability.last_checked = datetime.now(timezone.utc)
