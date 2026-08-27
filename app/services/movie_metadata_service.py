"""Safe TMDB enrichment for existing movie records only."""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.movie import Movie
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
from app.services.tmdb.movie_service import TMDbMovieService
from app.services.artwork_service import ArtworkService


class MovieMetadataService:
    """Enriches an existing movie without changing its identity or deleting valid data."""

    def __init__(self, db: Session):
        self.db = db
        self.tmdb = TMDbMovieService()
        self.artwork = ArtworkService()

    def enrich_movie(self, movie: Movie) -> Movie:
        payload = self.tmdb.get_rich_movie_details(movie.tmdb_id)
        self._update_movie_scalars(movie, payload)
        self._upsert_external_ids(movie, payload.get("external_ids", {}))
        self._upsert_alternative_titles(movie, payload.get("alternative_titles", {}))
        self._upsert_credits(movie, payload.get("credits", {}))
        self._upsert_keywords(movie, payload.get("keywords", {}))
        self._upsert_production(movie, payload)
        self._upsert_releases(movie, payload.get("release_dates", {}))
        self._upsert_images(movie, payload.get("images", {}))
        self.db.flush()
        for image in self.db.query(MovieImage).filter_by(movie_id=movie.id, is_primary=True).all():
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
        for field in ("tagline", "budget", "revenue", "status", "runtime", "popularity", "vote_average", "vote_count"):
            target = "runtime_minutes" if field == "runtime" else field
            self._set_if_present(movie, target, payload.get(field))
        collection = payload.get("belongs_to_collection") or {}
        if collection:
            self._set_if_present(movie, "collection_tmdb_id", collection.get("id"))
            self._set_if_present(movie, "collection_name", collection.get("name"))
            self._set_if_present(movie, "collection_poster_path", collection.get("poster_path"))
            self._set_if_present(movie, "collection_backdrop_path", collection.get("backdrop_path"))

    def _upsert_alternative_titles(self, movie: Movie, data: dict) -> None:
        for item in data.get("titles", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            country = item.get("iso_3166_1") or None
            record = self.db.query(AlternativeTitle).filter_by(movie_id=movie.id, country=country, title=title).first()
            if not record:
                self.db.add(AlternativeTitle(movie_id=movie.id, country=country, title=title, title_type=item.get("type") or None))

    def _upsert_external_ids(self, movie: Movie, data: dict) -> None:
        values = {"imdb": data.get("imdb_id"), "wikidata": data.get("wikidata_id"), "facebook": data.get("facebook_id"), "instagram": data.get("instagram_id"), "twitter": data.get("twitter_id")}
        for provider, external_id in values.items():
            if not external_id:
                continue
            record = self.db.query(ExternalId).filter_by(movie_id=movie.id, provider=provider).first()
            if record:
                record.external_id = external_id
            else:
                self.db.add(ExternalId(movie_id=movie.id, provider=provider, external_id=external_id))

    def _person(self, item: dict) -> Person:
        person = self.db.query(Person).filter_by(tmdb_id=item["id"]).first()
        if not person:
            person = Person(tmdb_id=item["id"], name=item.get("name") or "Unknown")
            self.db.add(person)
            self.db.flush()
        self._set_if_present(person, "name", item.get("name"))
        self._set_if_present(person, "profile_path", item.get("profile_path"))
        self._set_if_present(person, "known_for_department", item.get("known_for_department"))
        return person

    def _upsert_credits(self, movie: Movie, data: dict) -> None:
        for credit_type, items in (("cast", data.get("cast", [])), ("crew", data.get("crew", []))):
            for item in items:
                if not item.get("id"):
                    continue
                person = self._person(item)
                filters = {"movie_id": movie.id, "person_id": person.id, "credit_type": credit_type, "job": item.get("job"), "character": item.get("character")}
                credit = self.db.query(MovieCredit).filter_by(**filters).first()
                if not credit:
                    credit = MovieCredit(**filters)
                    self.db.add(credit)
                credit.tmdb_credit_id = item.get("credit_id") or credit.tmdb_credit_id
                credit.cast_order = item.get("order") if credit_type == "cast" else credit.cast_order
                credit.department = item.get("department") or credit.department

    def _upsert_keywords(self, movie: Movie, data: dict) -> None:
        for item in data.get("keywords", []):
            if not item.get("id") or not item.get("name"):
                continue
            keyword = self.db.query(Keyword).filter_by(tmdb_id=item["id"]).first()
            if not keyword:
                keyword = Keyword(tmdb_id=item["id"], name=item["name"])
                self.db.add(keyword)
                self.db.flush()
            if not self.db.query(MovieKeyword).filter_by(movie_id=movie.id, keyword_id=keyword.id).first():
                self.db.add(MovieKeyword(movie_id=movie.id, keyword_id=keyword.id))

    def _upsert_production(self, movie: Movie, payload: dict) -> None:
        for item in payload.get("production_companies", []):
            if not item.get("id") or not item.get("name"):
                continue
            company = self.db.query(ProductionCompany).filter_by(tmdb_id=item["id"]).first()
            if not company:
                company = ProductionCompany(tmdb_id=item["id"], name=item["name"])
                self.db.add(company)
                self.db.flush()
            self._set_if_present(company, "logo_path", item.get("logo_path"))
            self._set_if_present(company, "origin_country", item.get("origin_country"))
            if not self.db.query(MovieProductionCompany).filter_by(movie_id=movie.id, production_company_id=company.id).first():
                self.db.add(MovieProductionCompany(movie_id=movie.id, production_company_id=company.id))
        for item in payload.get("production_countries", []):
            code = item.get("iso_3166_1")
            if not code or not item.get("name"):
                continue
            country = self.db.query(ProductionCountry).filter_by(iso_3166_1=code).first()
            if not country:
                country = ProductionCountry(iso_3166_1=code, name=item["name"])
                self.db.add(country)
                self.db.flush()
            if not self.db.query(MovieProductionCountry).filter_by(movie_id=movie.id, production_country_id=country.id).first():
                self.db.add(MovieProductionCountry(movie_id=movie.id, production_country_id=country.id))

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
                record = self.db.query(MovieReleaseDate).filter_by(movie_id=movie.id, country=country, release_date=release_date, release_type=release_type).first()
                if not record:
                    self.db.add(MovieReleaseDate(movie_id=movie.id, country=country, release_date=release_date, release_type=release_type, certification=item.get("certification") or None, note=item.get("note") or None))

    def _upsert_images(self, movie: Movie, data: dict) -> None:
        for image_type, items in (("poster", data.get("posters", [])), ("backdrop", data.get("backdrops", [])), ("logo", data.get("logos", []))):
            for index, item in enumerate(items):
                path = item.get("file_path")
                if not path:
                    continue
                source_id = path
                record = self.db.query(MovieImage).filter_by(movie_id=movie.id, image_type=image_type, source="tmdb", source_id=source_id).first()
                if not record:
                    record = MovieImage(movie_id=movie.id, image_type=image_type, source="tmdb", source_id=source_id)
                    self.db.add(record)
                record.original_url = f"https://image.tmdb.org/t/p/original{path}"
                record.language = item.get("iso_639_1") or None
                record.width = item.get("width")
                record.height = item.get("height")
                record.aspect_ratio = item.get("aspect_ratio")
                record.is_primary = index == 0

    def _upsert_tmdb_rating(self, movie: Movie, payload: dict) -> None:
        record = self.db.query(MovieRating).filter_by(movie_id=movie.id, source="tmdb").first()
        if not record:
            record = MovieRating(movie_id=movie.id, source="tmdb")
            self.db.add(record)
        record.rating = payload.get("vote_average")
        record.vote_count = payload.get("vote_count")
        record.last_updated_at = datetime.now(timezone.utc)
