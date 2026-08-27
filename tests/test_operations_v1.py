from datetime import date

from PIL import Image
import pytest

from app.models.movie import Movie
from app.models.movie_metadata import Person
from app.models.operations import DataQualityIssue, OperationState
from app.models.ott_availability import OttAvailability
from app.services.image_fallback import ImageFallbackService
from app.services.notification_service import NotificationService
from app.services.operations import DataHealthService, OttResearchService
from app.core.rate_limit import limit
from fastapi import HTTPException
from starlette.requests import Request


def test_data_health_cursor_and_deduplication(database):
    result = DataHealthService(database).scan(batch_size=1)
    assert result["scanned"] == 1
    assert database.query(OperationState).filter_by(name="data_health").one().cursor == 1
    DataHealthService(database).scan(batch_size=1)
    assert database.query(DataQualityIssue).filter_by(movie_id=1, issue_type="missing_cast", resolved_at=None).count() <= 1


def test_image_validation_and_whole_database_cursors(database, tmp_path, monkeypatch):
    good = tmp_path / "good.png"; Image.new("RGB", (4, 4)).save(good)
    broken = tmp_path / "broken.png"; broken.write_bytes(b"not an image")
    service = ImageFallbackService(database)
    assert service.validate(str(good)) == "HEALTHY"
    assert service.validate(str(broken)) == "BROKEN"
    assert service.validate(str(tmp_path / "missing.png")) == "MISSING"
    monkeypatch.setattr(service, "recover_movie", lambda movie, image_type: {"status": "HEALTHY", "type": image_type})
    monkeypatch.setattr(service, "recover_person", lambda person: {"status": "HEALTHY", "type": "profile"})
    result = service.scan(batch_size=10)
    assert result["movies"] == 2 and result["people"] == 2
    assert database.query(OperationState).filter_by(name="image_health_movies").one().cursor == 2
    assert database.query(OperationState).filter_by(name="image_health_people").one().cursor == 2


def test_ott_confidence_canonical_conflict_and_backoff(database, monkeypatch):
    monkeypatch.setattr(NotificationService, "notify", lambda *args, **kwargs: True)
    service = OttResearchService(database, confirmation_threshold=85)
    weak = service.record_evidence(2, platform="BlogStream", source_url="https://blog.invalid/item", confidence=35, source_rank="unknown")
    assert weak.status == "POSSIBLE" and database.query(OttAvailability).filter_by(movie_id=2, provider="BlogStream").first() is None
    confirmed = service.record_evidence(2, platform="Prime Video", release_date=date.today(), source_url="https://primevideo.com/example", confidence=95, source_rank="official_platform")
    assert confirmed.status == "CONFIRMED"
    assert database.query(OttAvailability).filter_by(movie_id=2, provider="Prime Video").one().status == "confirmed"
    conflict = service.record_evidence(2, platform="Netflix", release_date=date.today(), source_url="https://netflix.com/example", confidence=95, source_rank="official_platform")
    assert conflict.status == "CONFLICTING"
    assert service.next_check_for("FAILED", attempts=4) > service.next_check_for("QUEUED")


def test_notifications_continue_after_channel_failure_and_dedupe(database, monkeypatch):
    service = NotificationService(database)
    monkeypatch.setattr(service, "_discord", lambda message: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(service, "_telegram", lambda message: True)
    monkeypatch.setattr(service, "_email", lambda message: True)
    assert service.notify("Important worker failure", "high", "worker:test", 60) is True
    assert service.notify("Important worker failure", "high", "worker:test", 60) is False


def test_rate_limit_rejects_after_fixed_window(monkeypatch):
    class FakeRedis:
        count = 0
        def incr(self, _): self.count += 1; return self.count
        def expire(self, *_): return True
    fake = FakeRedis()
    monkeypatch.setattr("app.core.rate_limit.redis.from_url", lambda *args, **kwargs: fake)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1000)})
    limit(request, "test", 1, 60)
    with pytest.raises(HTTPException) as raised:
        limit(request, "test", 1, 60)
    assert raised.value.status_code == 429
