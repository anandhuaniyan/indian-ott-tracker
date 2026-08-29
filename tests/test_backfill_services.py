"""Offline coverage for accelerated, resumable data-repair workflows."""

from datetime import date, datetime, timedelta, timezone

from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieRating, MovieReleaseDate, MovieTrailer, Person
from app.models.operations import BackfillRecord, OttEvidence
from app.services.backfill import IMDbBackfillService, MetadataBackfillService, OttQueueBackfillService, PersonBackfillService, SingleMovieRepairService, TrailerBackfillService
from app.config.settings import settings
from app.services.movie_metadata_service import MovieMetadataService
from app.services.ott_providers import GoogleProgrammableSearchProvider
from app.services.rating_provider import RatingResult


class FakeRatingProvider:
    source = "IMDb"

    def fetch(self, imdb_id):
        return RatingResult(8.1, 4321, imdb_id, datetime.now(timezone.utc))


def test_metadata_backfill_prioritizes_missing_credits_and_checkpoints(database, monkeypatch):
    class FakeMetadata:
        def __init__(self, db): self.db = db
        def enrich_movie(self, movie):
            person = self.db.query(Person).filter_by(tmdb_id=99).first() or Person(tmdb_id=99, name="Repair Actor", profile_path="/profile.jpg")
            self.db.add(person); self.db.flush()
            self.db.add_all([MovieCredit(movie_id=movie.id, person_id=person.id, credit_type="cast", character="Lead"), MovieCredit(movie_id=movie.id, person_id=person.id, credit_type="crew", job="Director")])
            self.db.add(ExternalId(movie_id=movie.id, provider="imdb", external_id="tt7654321"))
            movie.poster_path = "/poster.jpg"; movie.backdrop_path = "/backdrop.jpg"
            self.db.commit(); return movie

    monkeypatch.setattr("app.services.backfill.MovieMetadataService", FakeMetadata)
    result = MetadataBackfillService(database).run(batch_size=1)
    repaired = database.get(Movie, 2)
    assert result["processed"] == result["succeeded"] == 1
    assert database.query(MovieCredit).filter_by(movie_id=repaired.id, credit_type="cast").count() == 1
    assert database.query(MovieCredit).filter_by(movie_id=repaired.id, credit_type="crew").count() == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.metadata_backfill", entity_id=repaired.id, status="DONE").one()


def test_person_backfill_saves_profile_and_practical_metadata(database, monkeypatch):
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_person_details", lambda self, person_id: {"name": "Example Actor", "profile_path": "/real-profile.jpg", "biography": "Stored biography", "birthday": "1980-01-02", "place_of_birth": "Kerala, India", "external_ids": {"imdb_id": "nm1234567"}})
    result = PersonBackfillService(database).run(batch_size=1)
    person = database.query(Person).order_by(Person.id).first()
    assert result["succeeded"] == 1
    assert person.profile_path == "/real-profile.jpg" and person.biography == "Stored biography"
    assert person.imdb_id == "nm1234567" and person.birthday.isoformat() == "1980-01-02"


def test_trailer_backfill_is_resumable_prioritized_and_idempotent(database, monkeypatch):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    calls = []

    def videos(_self, tmdb_id):
        calls.append(tmdb_id)
        return {"results": [{"site": "YouTube", "key": f"Video{tmdb_id:06d}", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]}

    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", videos)
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["succeeded"] == 2 and result["complete"] is True
    assert database.query(MovieTrailer).filter_by(is_primary=True).count() == 2
    assert TrailerBackfillService(database).run(batch_size=10)["processed"] == 0
    assert len(calls) == 2


def test_imdb_backfill_uses_approved_provider_and_does_not_fabricate(database):
    database.add(ExternalId(movie_id=2, provider="imdb", external_id="tt7654321")); database.commit()
    result = IMDbBackfillService(database, FakeRatingProvider()).run(batch_size=10)
    rating = database.query(MovieRating).filter_by(movie_id=2).one()
    assert result["configured"] is True and result["succeeded"] == 1
    assert rating.source == "IMDb" and rating.rating == 8.1 and rating.vote_count == 4321


def test_ott_queue_backfill_covers_missing_provider_or_date(database):
    result = OttQueueBackfillService(database).run(batch_size=10)
    queued_ids = {item.movie_id for item in database.query(OttEvidence).filter_by(status="QUEUED")}
    assert result["processed"] == 1 and queued_ids == {1}
    assert database.get(Movie, 2).ott_research_eligibility == "WAITING_RELEASE"


def test_google_programmable_search_uses_json_api_and_disambiguated_queries(database, monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def raise_for_status(self): return None
        def json(self): return {"items": [{"title": "Example Film streaming", "link": "https://www.netflix.com/title/123", "snippet": "Available August 27, 2026"}]}
    def fake_get(url, **kwargs): calls.append(kwargs["params"]["q"]); return Response()
    monkeypatch.setattr("app.services.ott_providers.httpx.get", fake_get)
    results = GoogleProgrammableSearchProvider("key", "engine").search(database.get(Movie, 1))
    assert len(calls) == 3 and all('"Example Film"' in query for query in calls)
    assert len(results) == 1 and results[0]["platform"] == "Netflix" and results[0]["release_date"] == "2026-08-27"


def test_single_movie_repair_populates_credits_profiles_rating_and_ott_queue(database, monkeypatch):
    movie = Movie(tmdb_id=303, title="Needs Repair", original_language="ml")
    database.add(movie); database.commit()

    def fake_enrich(self, target):
        actor = Person(tmdb_id=3030, name="Repaired Person")
        self.db.add(actor); self.db.flush()
        self.db.add_all([MovieCredit(movie_id=target.id, person_id=actor.id, credit_type="cast", character="Lead"), MovieCredit(movie_id=target.id, person_id=actor.id, credit_type="crew", job="Director")])
        self.db.add(ExternalId(movie_id=target.id, provider="imdb", external_id="tt3030303"))
        self.db.add(MovieReleaseDate(movie_id=target.id, country="IN", release_date=date.today() - timedelta(days=10), release_type="3"))
        target.poster_path = "/poster.jpg"; target.backdrop_path = "/backdrop.jpg"; self.db.commit(); return target

    monkeypatch.setattr("app.services.backfill.MovieMetadataService.enrich_movie", fake_enrich)
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_person_details", lambda self, person_id: {"profile_path": "/person.jpg", "biography": "Bio", "external_ids": {"imdb_id": "nm3030303"}})
    monkeypatch.setattr("app.services.backfill.ImageFallbackService.recover_movie", lambda self, movie, image_type: {"status": "HEALTHY", "type": image_type})
    result = SingleMovieRepairService(database, FakeRatingProvider()).repair(movie.id)
    repaired_person = database.query(Person).filter_by(tmdb_id=3030).one()
    assert result["metadata"] == "updated" and result["imdb"] == "updated" and result["ott_queued"] is True
    assert database.query(MovieCredit).filter_by(movie_id=movie.id).count() == 2
    assert repaired_person.profile_path == "/person.jpg"
    assert database.query(MovieRating).filter_by(movie_id=movie.id, source="IMDb").one().rating == 8.1


def test_metadata_enrichment_promotes_core_paths_relations_and_tmdb_watch_providers(database):
    movie = database.get(Movie, 2); service = MovieMetadataService(database)
    payload = {
        "title": "Future Film", "poster_path": "/provider-poster.jpg", "backdrop_path": "/provider-backdrop.jpg",
        "release_date": "2027-01-02", "original_language": "ml",
        "genres": [{"id": 18, "name": "Drama"}],
        "spoken_languages": [{"iso_639_1": "ml", "english_name": "Malayalam", "name": "മലയാളം"}],
    }
    service._update_movie_scalars(movie, payload); service._upsert_genres_and_languages(movie, payload)
    service._upsert_watch_providers(movie, {"results": {"IN": {"link": "https://www.themoviedb.org/movie/102/watch", "flatrate": [{"provider_name": "Provider One", "logo_path": "/provider.png"}]}}})
    database.commit()
    assert movie.poster_path == "/provider-poster.jpg" and movie.backdrop_path == "/provider-backdrop.jpg"
    assert movie.genres[0].name == "Drama" and movie.languages[0].iso_639_1 == "ml"
    assert movie.ott_availabilities[0].provider == "Provider One" and movie.ott_availabilities[0].watch_type == "subscription"


def test_external_id_upsert_skips_shared_social_identity_without_failing(database):
    database.add(ExternalId(movie_id=1, provider="instagram", external_id="shared-account")); database.commit()
    service = MovieMetadataService(database)
    service._upsert_external_ids(database.get(Movie, 2), {"instagram_id": "shared-account"})
    database.commit()
    assert database.query(ExternalId).filter_by(provider="instagram", external_id="shared-account").count() == 1
