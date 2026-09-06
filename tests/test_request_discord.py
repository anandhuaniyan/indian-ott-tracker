from datetime import date
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.models.operations import (
    ContactRequest,
    MovieRequest,
    RequestNotificationDelivery,
)
from app.services.request_discord import DiscordDeliveryError, RequestDiscordService
from app.services.contact_requests import (
    ContactEmailDeliveryError,
    ContactRequestNotificationService,
)


def _movie(database):
    item = MovieRequest(
        request_id="REQ-DISCORD",
        movie_name="Submitted title",
        verified_title="Verified title",
        original_title="Original title",
        external_movie_id=987,
        imdb_id="tt1234567",
        release_year=2026,
        verified_release_date=date(2026, 9, 1),
        verified_language_name="Malayalam",
        email="private@example.test",
        whatsapp_phone="+65 8000 0000",
        details="Private comments",
        status="PENDING",
    )
    database.add(item)
    database.commit()
    return item


def test_movie_discord_delivery_is_deduplicated_and_records_success(database, monkeypatch):
    item = _movie(database)
    calls = []
    monkeypatch.setattr(settings, "DISCORD_BOT_ENDPOINT", "http://127.0.0.1:8765/movie-events")
    monkeypatch.setattr(settings, "DISCORD_BOT_SHARED_SECRET", "private-shared-secret")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(
        "app.services.notification_service.httpx.post",
        lambda url, **kwargs: calls.append((url, kwargs))
        or SimpleNamespace(raise_for_status=lambda: None),
    )
    service = RequestDiscordService(database)
    first = service.create(
        request_id=item.request_id,
        request_kind="MOVIE_REQUEST",
        notification_type="MOVIE_REQUEST_SUBMITTED",
        event_key="SUBMITTED",
    )
    duplicate = service.create(
        request_id=item.request_id,
        request_kind="MOVIE_REQUEST",
        notification_type="MOVIE_REQUEST_SUBMITTED",
        event_key="SUBMITTED",
    )
    assert first.id == duplicate.id
    assert service.deliver(first.id)["status"] == "SENT"
    assert service.deliver(first.id)["deduplicated"] is True
    assert len(calls) == 1
    message = calls[0][1]["json"]["content"]
    for expected in (
        "🎬 NEW MOVIE REQUEST",
        "Request ID: REQ-DISCORD",
        "Movie Name: Verified title",
        "Original Movie Name: Original title",
        "TMDB ID: 987",
        "IMDb ID: tt1234567",
        "OTT Platform: Researching",
        "Requester Email: private@example.test",
        "Requester WhatsApp / Phone: +65 8000 0000",
        "Comments: Private comments",
        "Request Status: PENDING",
    ):
        assert expected in message
    database.refresh(first)
    assert first.status == "SENT" and first.attempt_count == 1 and first.sent_at


def test_access_message_and_not_configured_history(database, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_BOT_ENDPOINT", "")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    item = ContactRequest(
        request_id="WEB-ACCESS",
        request_type="ACCESS_REQUEST",
        name=None,
        whatsapp="+65 8111 1111",
        comment="Please review my access request.",
        status="NEW",
    )
    database.add(item)
    database.commit()
    service = RequestDiscordService(database)
    record = service.schedule_submission(item)
    assert record.status == "NOT_CONFIGURED"
    assert item.discord_status == "NOT_CONFIGURED"
    message = service.contact_message(item)
    assert message.startswith("🔐 NEW ACCESS REQUEST")
    assert "Name: Not supplied" in message
    assert "Reason / Comment: Please review my access request." in message


def test_failed_delivery_is_saved_with_sanitized_error_for_retry(database, monkeypatch):
    item = _movie(database)
    monkeypatch.setattr(settings, "DISCORD_BOT_ENDPOINT", "http://127.0.0.1:8765/movie-events")
    monkeypatch.setattr(settings, "DISCORD_BOT_SHARED_SECRET", "private-shared-secret")
    service = RequestDiscordService(database)
    record = service.create(
        request_id=item.request_id,
        request_kind="MOVIE_REQUEST",
        notification_type="MOVIE_REQUEST_SUBMITTED",
        event_key="SUBMITTED",
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService._discord",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Authorization: Bearer private-shared-secret")),
    )
    with pytest.raises(DiscordDeliveryError):
        service.deliver(record.id)
    database.refresh(record)
    assert record.status == "FAILED"
    assert record.attempted_at and record.attempt_count == 1
    assert "private-shared-secret" not in record.sanitized_error
    assert "[redacted]" in record.sanitized_error
    assert database.query(RequestNotificationDelivery).count() == 1


def test_contact_email_is_queued_delivered_and_deduplicated(database, monkeypatch):
    item = ContactRequest(
        request_id="WEB-EMAIL",
        request_type="WEBSITE_ISSUE",
        email="viewer@example.test",
        comment="The calendar link needs review.",
        status="NEW",
    )
    database.add(item)
    database.commit()
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@example.test")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "private-smtp-password")
    queued = []
    monkeypatch.setattr(
        "app.workers.celery_app.celery_app.send_task",
        lambda name, args=None, **_kwargs: queued.append((name, args)),
    )
    sent = []
    monkeypatch.setattr(
        ContactRequestNotificationService,
        "_deliver_message",
        staticmethod(lambda message: sent.append(message)),
    )
    service = ContactRequestNotificationService(database)
    record = service.schedule_email(item, "RECEIVED")
    duplicate = service.schedule_email(item, "RECEIVED")
    assert record.id == duplicate.id
    assert queued[0] == ("notifications.contact_request_email", [record.id])
    assert service.deliver(record.id)["status"] == "SENT"
    assert service.deliver(record.id)["deduplicated"] is True
    assert len(sent) == 1
    assert sent[0].get_body(preferencelist=("plain",)).get_content().startswith(
        "Request: WEB-EMAIL"
    )
    assert sent[0].get_body(preferencelist=("html",)) is not None
    database.refresh(item)
    assert item.receipt_email_status == "SENT"


def test_contact_email_failure_is_safe_and_recoverable(database, monkeypatch):
    item = ContactRequest(
        request_id="WEB-EMAIL-FAIL",
        request_type="ACCESS_REQUEST",
        email="viewer@example.test",
        comment="Please review access.",
        status="NEW",
    )
    database.add(item)
    database.commit()
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@example.test")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "private-smtp-password")
    monkeypatch.setattr(
        "app.workers.celery_app.celery_app.send_task", lambda *_args, **_kwargs: None
    )
    service = ContactRequestNotificationService(database)
    record = service.schedule_email(item, "RECEIVED")
    monkeypatch.setattr(
        ContactRequestNotificationService,
        "_deliver_message",
        staticmethod(
            lambda _message: (_ for _ in ()).throw(
                RuntimeError("password=private-smtp-password")
            )
        ),
    )
    with pytest.raises(ContactEmailDeliveryError):
        service.deliver(record.id)
    database.refresh(record)
    assert record.status == "FAILED" and record.attempt_count == 1
    assert "private-smtp-password" not in record.sanitized_error
    assert database.query(ContactRequest).filter_by(request_id=item.request_id).count() == 1
    record.attempted_at = None
    database.commit()
    queued = []
    monkeypatch.setattr(
        "app.workers.celery_app.celery_app.send_task",
        lambda name, args=None, **_kwargs: queued.append((name, args)),
    )
    assert service.recover() == [record.id]
    assert queued == [("notifications.contact_request_email", [record.id])]
