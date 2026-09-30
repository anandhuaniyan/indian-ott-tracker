from datetime import datetime, timedelta, timezone

from app.models.operations import BackfillRecord
from app.services.backfill import TrailerBackfillService


def _make_record(db, entity_id, status, updated_at):
    record = BackfillRecord(
        operation="tmdb.trailer_backfill",
        entity_type="movie",
        entity_id=entity_id,
        status=status,
        attempts=1,
        last_error="previous failure",
        last_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        updated_at=updated_at,
    )
    db.add(record)
    db.commit()
    return record


def test_stale_running_record_recovered_to_pending(database, monkeypatch):
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=45)
    _make_record(database, 40097, "RUNNING", stale_time)
    service = TrailerBackfillService(database)
    count = service._recover_stale_running()
    assert count == 1
    record = database.query(BackfillRecord).filter_by(entity_id=40097).one()
    assert record.status == "PENDING"
    assert record.attempts == 1
    assert record.last_error == "previous failure"
    assert record.last_attempt_at is not None


def test_recent_running_record_not_recovered(database, monkeypatch):
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    _make_record(database, 39874, "RUNNING", recent_time)
    service = TrailerBackfillService(database)
    count = service._recover_stale_running()
    assert count == 0
    record = database.query(BackfillRecord).filter_by(entity_id=39874).one()
    assert record.status == "RUNNING"


def test_non_running_status_records_not_affected(database, monkeypatch):
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=120)
    done = _make_record(database, 1001, "DONE", stale_time)
    failed = _make_record(database, 1002, "FAILED", stale_time)
    pending = _make_record(database, 1003, "PENDING", stale_time)
    service = TrailerBackfillService(database)
    count = service._recover_stale_running()
    assert count == 0
    assert done.status == "DONE"
    assert failed.status == "FAILED"
    assert pending.status == "PENDING"


def test_stale_recovery_invoked_at_run_start(database, monkeypatch):
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=60)
    _make_record(database, 99999, "RUNNING", stale_time)
    monkeypatch.setattr(
        "app.services.backfill.TrailerBackfillService._reserve_youtube_search",
        lambda self, state: False,
    )
    monkeypatch.setattr(
        "app.services.backfill.TMDbMovieService.get_movie_videos",
        lambda self, tmdb_id, language=None: {"results": []},
    )
    monkeypatch.setattr(
        "app.services.backfill.settings.TMDB_API_KEY", "test-key"
    )
    monkeypatch.setattr(
        "app.services.backfill.settings.YOUTUBE_API_KEY", ""
    )
    service = TrailerBackfillService(database)
    service._recover_stale_running()
    record = (
        database.query(BackfillRecord)
        .filter_by(entity_id=99999)
        .one()
    )
    assert record.status == "PENDING"


def test_permanent_tmdb_404_failed_record_not_eligible_for_retry(database, monkeypatch):
    movie_id = 8801
    record = BackfillRecord(
        operation="tmdb.trailer_backfill",
        entity_type="movie",
        entity_id=movie_id,
        status="FAILED",
        attempts=1,
        last_error="External metadata request failed with HTTP 404 for /movie/8801/videos",
        last_attempt_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    database.add(record)
    database.commit()
    service = TrailerBackfillService(database)
    eligible = database.query(BackfillRecord.entity_id).filter(
        service._eligible("movie", 8801)
    ).first()
    assert eligible is None


def test_temporary_failure_failed_record_still_eligible_for_retry(database):
    movie_id = 8802
    record = BackfillRecord(
        operation="tmdb.trailer_backfill",
        entity_type="movie",
        entity_id=movie_id,
        status="FAILED",
        attempts=1,
        last_error="Connection timeout",
        last_attempt_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    database.add(record)
    database.commit()
    service = TrailerBackfillService(database)
    eligible = database.query(service._eligible("movie", 8802)).first()
    assert eligible is not None


def test_failed_record_at_max_attempts_not_eligible(database):
    movie_id = 8803
    record = BackfillRecord(
        operation="tmdb.trailer_backfill",
        entity_type="movie",
        entity_id=movie_id,
        status="FAILED",
        attempts=3,
        last_error="Connection timeout",
        last_attempt_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    database.add(record)
    database.commit()
    service = TrailerBackfillService(database)
    eligible = database.query(BackfillRecord.entity_id).filter(
        service._eligible("movie", 8803)
    ).first()
    assert eligible is None
