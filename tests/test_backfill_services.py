"""Offline coverage for accelerated, resumable data-repair workflows."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieImage, MovieRating, MovieReleaseDate, MovieTrailer, Person
from app.models.operations import BackfillRecord, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.backfill import IMDbBackfillService, MetadataBackfillService, OttQueueBackfillService, PersonBackfillService, SingleMovieRepairService, TrailerBackfillService
from app.services.trailers import TrailerService
from app.services.youtube_trailer import YouTubeTrailerService, accepted_title, YouTubeTemporaryError, YouTubeInvalidKey, YouTubeQuotaExceeded
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

    def videos(_self, tmdb_id, _language=None):
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


def test_tmdb_watch_provider_refresh_prefers_existing_normalized_row(database):
    movie = database.get(Movie, 2)
    database.add_all([
        OttAvailability(movie_id=movie.id, provider="Amazon Prime Video with Ads", country="IN", watch_type="subscription"),
        OttAvailability(movie_id=movie.id, provider="Prime Video", country="IN", watch_type="subscription"),
    ])
    database.commit()

    MovieMetadataService(database)._upsert_watch_providers(movie, {
        "results": {"IN": {
            "link": "https://www.themoviedb.org/movie/102/watch",
            "flatrate": [{"provider_name": "Amazon Prime Video", "logo_path": "/prime.png"}],
        }}
    })
    database.commit()

    rows = database.query(OttAvailability).filter_by(movie_id=movie.id, watch_type="subscription").all()
    assert len(rows) == 2
    assert next(row for row in rows if row.provider == "Prime Video").provider_logo == "/prime.png"


def test_metadata_deduplicates_alias_providers_and_repeated_images_in_one_payload(database):
    movie = database.get(Movie, 2)
    service = MovieMetadataService(database, cache_artwork=False)
    service._upsert_watch_providers(movie, {
        "results": {"IN": {
            "flatrate": [
                {"provider_name": "Amazon Prime Video", "logo_path": "/prime-1.png"},
                {"provider_name": "Prime Video", "logo_path": "/prime-2.png"},
            ],
        }}
    })
    service._upsert_images(movie, {
        "logos": [
            {"file_path": "/same.png", "iso_639_1": "en"},
            {"file_path": "/same.png", "iso_639_1": "en"},
        ]
    })
    database.commit()

    assert database.query(OttAvailability).filter_by(
        movie_id=movie.id, provider="Prime Video", country="IN", watch_type="subscription"
    ).count() == 1
    assert database.query(MovieImage).filter_by(
        movie_id=movie.id, image_type="logo", source="tmdb", source_id="/same.png"
    ).count() == 1


def test_external_id_upsert_skips_shared_social_identity_without_failing(database):
    database.add(ExternalId(movie_id=1, provider="instagram", external_id="shared-account")); database.commit()
    service = MovieMetadataService(database)
    service._upsert_external_ids(database.get(Movie, 2), {"instagram_id": "shared-account"})
    database.commit()
    assert database.query(ExternalId).filter_by(provider="instagram", external_id="shared-account").count() == 1


def test_trailer_backfill_uses_youtube_search_fallback_when_tmdb_empty(database, monkeypatch):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")

    def videos(_self, tmdb_id, _language=None):
        return {"results": []}

    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", videos)
    monkeypatch.setattr(
        YouTubeTrailerService,
        "search",
        lambda self, movie: [
            {
                "site": "YouTube",
                "key": f"Yt{movie.tmdb_id:09d}",
                "type": "Trailer",
                "name": f"{movie.title} Official Trailer",
                "official": False,
                "iso_639_1": "ml",
            }
        ],
    )
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["succeeded"] == 2
    assert database.query(MovieTrailer).filter_by(video_key="Yt000000101").count() == 1
    assert database.query(MovieTrailer).filter_by(video_key="Yt000000102").count() == 1


def test_trailer_backfill_does_not_overwrite_or_recheck_existing_primary(database, monkeypatch):
    movie = database.get(Movie, 1)
    TrailerService(database).upsert(
        movie,
        {"results": [{"site": "YouTube", "key": "StaleKey123", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]},
        commit=True,
    )
    for item in database.query(MovieTrailer):
        item.last_checked_at = datetime.now(timezone.utc) - timedelta(days=settings.TRAILER_REFRESH_DAYS + 10)
    database.commit()

    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": []})
    monkeypatch.setattr(YouTubeTrailerService, "search", lambda self, movie: (_ for _ in ()).throw(AssertionError("search must not run")))
    monkeypatch.setattr(YouTubeTrailerService, "health_check", lambda self, key: (_ for _ in ()).throw(AssertionError("health check must not run")))

    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["processed"] == 1
    stored = database.query(MovieTrailer).filter_by(video_key="StaleKey123").one()
    assert stored.is_unavailable is False
    assert stored.is_primary is True


def test_trailer_backfill_stops_at_daily_youtube_search_limit(database, monkeypatch):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_SEARCH_DAILY_LIMIT", 1)
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": []})
    monkeypatch.setattr(YouTubeTrailerService, "search", lambda self, movie: [])

    result = TrailerBackfillService(database).run(batch_size=10)
    state = TrailerBackfillService(database).state()
    assert result["status"] == "WAITING_FOR_YOUTUBE_QUOTA"
    assert result["youtube_searches_used"] == 1
    assert state.status == "WAITING_FOR_YOUTUBE_QUOTA"
    assert state.details["youtube_searches_used"] == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="PENDING").count() == 1


def test_exhausted_quota_returns_before_processing_or_checkpoint_change(database, monkeypatch):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_SEARCH_DAILY_LIMIT", 90)
    service = TrailerBackfillService(database)
    state = service.state()
    state.cursor = 4321
    state.details = {
        "youtube_quota_date": service._youtube_quota_date(),
        "youtube_searches_used": 90,
        "last_processed_movie_id": 4321,
    }
    database.commit()
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider must not run")))
    result = service.run(batch_size=10)
    database.refresh(state)
    assert result["status"] == "WAITING_FOR_YOUTUBE_QUOTA"
    assert state.cursor == 4321
    assert state.details["youtube_searches_used"] == 90


def test_youtube_title_acceptance_is_strict(database):
    movie = database.get(Movie, 1)
    assert accepted_title("Example Film (2026) Official Malayalam Trailer", movie) is True
    assert accepted_title("Example Film Official Teaser", movie) is True
    assert accepted_title("Example Film Teaser | New Clip", movie) is False
    assert accepted_title("Example Film Movie Review", movie) is False
    assert accepted_title("Cats Reaction Video", movie) is False
    assert accepted_title("Another Film Official Trailer", movie) is False


@pytest.mark.parametrize("marker", [
    "Fan Trailer", "Concept Trailer", "AI Trailer", "Reaction", "Movie Review",
    "Official Song", "Lyric Video", "Movie Scene", "New Clip", "Cast Interview",
    "Behind the Scenes", "#Shorts",
])
def test_youtube_title_rejects_bad_content_patterns(database, marker):
    movie = database.get(Movie, 1)
    assert accepted_title(f"Example Film {marker} Trailer", movie) is False


def test_youtube_score_rejects_wrong_year_or_remake(database):
    movie = database.get(Movie, 1)
    item = {"snippet": {"title": "Example Film 1998 Official Trailer", "description": "", "channelTitle": "Studio Official"}}
    assert YouTubeTrailerService._score(item, movie) == -100


def test_youtube_score_accepts_independent_and_distributor_uploads(database):
    movie = database.get(Movie, 1)
    independent = {"snippet": {"title": "Example Film 2026 Official Trailer", "description": "", "channelTitle": "Jane Filmmaker"}}
    distributor = {"snippet": {"title": "Example Film 2026 Official Trailer", "description": "", "channelTitle": "Example Pictures"}}
    assert YouTubeTrailerService._score(independent, movie) >= 8
    assert YouTubeTrailerService._score(distributor, movie) > YouTubeTrailerService._score(independent, movie)


@pytest.mark.parametrize("video_items", [[], [{"id": "abcdefghijk", "snippet": {"title": "Example Film 2026 Official Trailer"}, "status": {"embeddable": False}, "contentDetails": {"duration": "PT2M"}}]])
def test_youtube_search_rejects_deleted_or_non_embeddable(database, monkeypatch, video_items):
    movie = database.get(Movie, 1)
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    search_items = [{"id": {"videoId": "abcdefghijk"}, "snippet": {"title": "Example Film 2026 Official Trailer", "description": "", "channelTitle": "Example Pictures"}}]

    class Response:
        def __init__(self, items): self.items = items
        def json(self): return {"items": self.items}

    monkeypatch.setattr(YouTubeTrailerService, "_request", lambda self, path, params: Response(search_items if path == "search" else video_items))
    assert YouTubeTrailerService().search(movie) == []


def test_trailer_upsert_reuses_same_movie_and_video(database):
    movie = database.get(Movie, 1)
    payload = {"results": [{"site": "YouTube", "key": "SameVideo01", "type": "Trailer", "name": "Example Film Official Trailer", "official": True}]}
    first = TrailerService(database)
    first.upsert(movie, payload, commit=True)
    second = TrailerService(database)
    second.upsert(database.get(Movie, 1), payload, commit=True)
    assert database.query(MovieTrailer).filter_by(movie_id=1, video_key="SameVideo01").count() == 1
    assert first.last_write_outcomes["created"] == 1
    assert second.last_write_outcomes["existing_video_reused"] == 1


def test_non_authoritative_youtube_result_preserves_valid_primary(database):
    movie = database.get(Movie, 1)
    service = TrailerService(database)
    service.upsert(movie, {"results": [{"site": "YouTube", "key": "OldPrimary1", "type": "Trailer", "name": "Example Film Official Trailer", "official": True}]}, commit=True)
    old = database.query(MovieTrailer).filter_by(video_key="OldPrimary1").one()
    service.upsert(database.get(Movie, 1), {"results": [{"site": "YouTube", "key": "NewResult01", "type": "Trailer", "name": "Example Film Trailer", "official": False}]}, commit=True, authoritative=False)
    database.refresh(old)
    assert old.is_primary is True and old.is_unavailable is False


def test_existing_backfill_record_is_updated_not_reinserted(database):
    service = TrailerBackfillService(database)
    first = service._record("movie", 1)
    database.commit()
    first_id = first.id
    service._start("movie", 1)
    second = service._record("movie", 1)
    assert second.id == first_id
    assert second.attempts == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", entity_id=1).count() == 1


def test_retry_after_successful_commit_is_idempotent(database):
    movie = database.get(Movie, 1)
    payload = {"results": [{"site": "YouTube", "key": "RetryVideo1", "type": "Trailer", "name": "Example Film Official Trailer", "official": True}]}
    TrailerService(database).upsert(movie, payload, commit=True)
    retried = TrailerService(database)
    primary = retried.upsert(database.get(Movie, 1), payload, commit=True)
    assert primary.video_key == "RetryVideo1"
    assert retried.last_write_outcomes == {"created": 0, "existing_video_reused": 1, "duplicate_skipped": 0}


def test_simulated_duplicate_insert_race_is_classified_not_raised(database, monkeypatch):
    movie = database.get(Movie, 1)
    existing = MovieTrailer(movie_id=movie.id, provider="YouTube", video_key="RaceVideo01")
    database.add(existing); database.commit()
    service = TrailerService(database)
    monkeypatch.setattr(service, "_existing_or_create", lambda movie_id, key: (existing, "duplicate_skipped"))
    service.upsert(movie, {"results": [{"site": "YouTube", "key": "RaceVideo01", "type": "Trailer", "name": "Example Film Trailer"}]}, commit=True)
    assert service.last_write_outcomes["duplicate_skipped"] == 1
    assert database.query(MovieTrailer).filter_by(movie_id=movie.id, video_key="RaceVideo01").count() == 1


def test_duplicate_reuse_does_not_increment_errors_and_advances_checkpoint(database, monkeypatch):
    movie = database.get(Movie, 1)
    database.add(MovieTrailer(movie_id=movie.id, provider="YouTube", video_key="Existing001", is_unavailable=True))
    database.commit()
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": [{"site": "YouTube", "key": "Existing001", "type": "Trailer", "name": "Example Film Official Trailer", "official": True}]})
    result = TrailerBackfillService(database).run(batch_size=1)
    state = TrailerBackfillService(database).state()
    assert result["failed"] == 0
    assert state.details["errors"] == 0
    assert state.details["existing_video_reused"] == 1
    assert state.cursor == movie.id


def test_real_integrity_error_rolls_back_and_next_movie_continues(database, monkeypatch):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": [{"site": "YouTube", "key": f"Real{tmdb_id:07d}", "type": "Trailer", "name": "Official Trailer", "official": True}]})
    original = TrailerService.upsert
    calls = {"count": 0}

    def fail_once(self, movie, data, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise IntegrityError("unrelated", {}, RuntimeError("real database failure"))
        return original(self, movie, data, **kwargs)

    monkeypatch.setattr(TrailerService, "upsert", fail_once)
    result = TrailerBackfillService(database).run(batch_size=2)
    state = TrailerBackfillService(database).state()
    assert result["failed"] == 1 and result["succeeded"] == 1
    assert state.details["errors"] == 1
    assert database.query(MovieTrailer).count() == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="FAILED").count() == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="DONE").count() == 1


def test_trailer_backfill_ends_in_DONE_when_upsert_returns_primary(database, monkeypatch):
    """Case 1: upsert creates a new primary trailer -> record ends DONE."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(
        "app.services.backfill.TMDbMovieService.get_movie_videos",
        lambda self, tmdb_id, language=None: {
            "results": [{"site": "YouTube", "key": f"Video{tmdb_id:06d}", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]
        },
    )
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["succeeded"] == 2
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="DONE").count() == 2
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0


def test_trailer_backfill_no_confident_counted_in_processed_not_succeeded(database, monkeypatch):
    """Case 3b: upsert returns None -> counted in processed, NOT in succeeded, no_confident_trailer > 0."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(
        "app.services.backfill.TMDbMovieService.get_movie_videos",
        lambda self, tmdb_id, language=None: {"results": [{"site": "YouTube", "key": "NoConf001", "type": "Teaser", "name": "Teaser Only", "official": False, "iso_639_1": "ml"}]},
    )
    monkeypatch.setattr(
        "app.services.backfill.TrailerService.upsert",
        lambda self, movie, data, **kwargs: None,
    )
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["succeeded"] == 0
    assert result["processed"] == 2
    assert result["no_confident_trailer"] == 2
    assert result["yield_control"] == "stop"
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="DONE").count() == 2
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0


def test_trailer_backfill_reuses_existing_primary_without_search(database, monkeypatch):
    """Case 2: movies already having valid primary trailers are skipped, no YouTube search invoked."""
    for tmdb_id in (101, 102):
        movie = database.query(Movie).filter_by(tmdb_id=tmdb_id).one()
        trailer = MovieTrailer(movie_id=movie.id, provider="youtube", video_key="ExistP00001", name="Official Trailer", video_type="Trailer", is_primary=True, is_unavailable=False)
        database.add(trailer)
    database.commit()
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(
        "app.services.backfill.TMDbMovieService.get_movie_videos",
        lambda self, tmdb_id, language=None: {"results": []},
    )
    monkeypatch.setattr(YouTubeTrailerService, "search", lambda self, movie: (_ for _ in ()).throw(AssertionError("search must not run")))
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["processed"] == 0
    assert result["succeeded"] == 0
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0


def test_trailer_backfill_ends_DONE_when_payload_exists_but_upsert_returns_none(database, monkeypatch):
    """Case 3: upsert returns None (no confident trailer) -> record ends DONE with no_result."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(
        "app.services.backfill.TMDbMovieService.get_movie_videos",
        lambda self, tmdb_id, language=None: {"results": [{"site": "YouTube", "key": "Teaser00001", "type": "Teaser", "name": "Teaser", "official": False, "iso_639_1": "ml"}]},
    )
    monkeypatch.setattr(
        "app.services.backfill.TrailerService.upsert",
        lambda self, movie, data, **kwargs: None,
    )
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["succeeded"] == 0
    records = database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill")
    assert records.filter_by(status="DONE").count() == 2
    assert records.filter_by(status="RUNNING").count() == 0


def test_trailer_backfill_ends_DONE_when_no_candidate_found(database, monkeypatch):
    """Case 4: TMDB empty, YouTube search returns no results -> record ends DONE with no_result."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": []})
    monkeypatch.setattr(YouTubeTrailerService, "search", lambda self, movie: [])
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["succeeded"] == 0
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="DONE").count() == 2
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0


def test_trailer_backfill_defers_when_quota_exhausted(database, monkeypatch):
    """Case 5: quota exhausted -> record deferred to PENDING, status WAITING_FOR_YOUTUBE_QUOTA."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_SEARCH_DAILY_LIMIT", 1)
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": []})
    monkeypatch.setattr(YouTubeTrailerService, "search", lambda self, movie: [])
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["status"] == "WAITING_FOR_YOUTUBE_QUOTA"
    assert result["youtube_searches_used"] == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="PENDING").count() == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0


def test_trailer_backfill_defers_on_temporary_youtube_error(database, monkeypatch):
    """Case 6: YouTubeTemporaryError -> record deferred to PENDING, continues to next movie."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": []})
    call_count = {"n": 0}

    def failing_search(self, movie):
        call_count["n"] += 1
        raise YouTubeTemporaryError("transient failure")

    monkeypatch.setattr(YouTubeTrailerService, "search", failing_search)
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["failed"] == 0
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="PENDING").count() == 2
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="FAILED").count() == 0


def test_trailer_backfill_marks_failed_on_permanent_youtube_error(database, monkeypatch):
    """Case 7: YouTubeInvalidKey -> record FAILED, status BLOCKED, batch stops."""
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured")
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "configured")
    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_videos", lambda self, tmdb_id, language=None: {"results": []})
    monkeypatch.setattr(YouTubeTrailerService, "search", lambda self, movie: (_ for _ in ()).throw(YouTubeInvalidKey("bad api key")))
    result = TrailerBackfillService(database).run(batch_size=10)
    assert result["failed"] == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="FAILED").count() == 1
    assert database.query(BackfillRecord).filter_by(operation="tmdb.trailer_backfill", status="RUNNING").count() == 0
    state = TrailerBackfillService(database).state()
    assert state.status == "BLOCKED"
