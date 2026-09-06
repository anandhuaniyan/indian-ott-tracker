"""Durable, idempotent Discord delivery for private user submissions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.operations import (
    ContactRequest,
    MovieRequest,
    NotificationLog,
    RequestNotificationDelivery,
)
from app.services.contact_requests import TYPE_LABELS
from app.services.notification_service import NotificationService
from app.services.release_status import best_canonical_ott


FINAL_STATUSES = {"SENT", "FAILED", "NOT_CONFIGURED"}


class DiscordDeliveryError(RuntimeError):
    """A sanitized retryable Discord delivery failure."""


def _value(value: object | None, fallback: str = "Not supplied", limit: int = 500) -> str:
    if value in (None, ""):
        return fallback
    text = str(value).strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


class RequestDiscordService:
    """Create, enqueue, deliver, and inspect Discord request notifications."""

    def __init__(self, db: Session):
        self.db = db

    def movie_message(self, item: MovieRequest, *, update: bool = False) -> str:
        movie = None
        if item.local_movie_id:
            from app.models.movie import Movie

            movie = self.db.get(Movie, item.local_movie_id)
        ott = best_canonical_ott(movie) if movie else None
        meaningful_ott = bool(ott and ott.provider and (ott.confidence or 0) > 0)
        title = item.verified_title or item.movie_name
        lines = [
            "🎬 MOVIE REQUEST OTT UPDATE" if update else "🎬 NEW MOVIE REQUEST",
            f"Request ID: {item.request_id}",
            f"Movie Name: {_value(title, 'Unknown')}",
        ]
        if item.original_title and item.original_title.casefold() != title.casefold():
            lines.append(f"Original Movie Name: {_value(item.original_title)}")
        lines.extend(
            [
                f"Year: {_value(item.release_year, 'Unknown')}",
                "Language: "
                + _value(
                    item.verified_language_name
                    or item.verified_original_language
                    or item.language,
                    "Unknown",
                ),
                f"TMDB ID: {_value(item.external_movie_id, 'Unknown')}",
                f"IMDb ID: {_value(item.imdb_id, 'Pending')}",
                f"Theatrical Release Date: {_value(item.verified_release_date, 'Unknown')}",
                f"OTT Platform: {_value(ott.provider if meaningful_ott else None, 'Researching')}",
                "OTT Release Date: "
                + _value(
                    ott.ott_release_date if meaningful_ott and ott.ott_release_date else None,
                    "Researching",
                ),
                "OTT Confidence: "
                + (f"{int(ott.confidence or 0)}%" if meaningful_ott else "Researching"),
                f"Requester Email: {_value(item.email)}",
                f"Requester WhatsApp / Phone: {_value(item.whatsapp_phone)}",
                f"Comments: {_value(item.details, limit=420)}",
                f"Request Time: {_value(item.created_at, 'Unknown')}",
                f"Request Status: {_value(item.status, 'Unknown')}",
                f"Local Movie ID: {_value(item.local_movie_id, 'Pending')}",
                f"Admin URL: {settings.SITE_URL.rstrip('/')}/admin/requests/{item.request_id}",
            ]
        )
        return "\n".join(lines)[:1900]

    def contact_message(self, item: ContactRequest) -> str:
        if item.request_type == "ACCESS_REQUEST":
            lines = [
                "🔐 NEW ACCESS REQUEST",
                f"Request ID: {item.request_id}",
                f"Name: {_value(item.name)}",
                f"WhatsApp: {_value(item.whatsapp)}",
                f"Phone: {_value(item.phone)}",
                f"Email: {_value(item.email)}",
                f"Reason / Comment: {_value(item.comment, limit=700)}",
                f"Submitted Time: {_value(item.created_at, 'Unknown')}",
                f"Status: {_value(item.status, 'Unknown')}",
            ]
        else:
            lines = [
                "⚠️ NEW WEBSITE ISSUE",
                f"Request ID: {item.request_id}",
                f"Issue Type: {TYPE_LABELS.get(item.request_type, item.request_type)}",
                f"Name: {_value(item.name)}",
                f"WhatsApp: {_value(item.whatsapp)}",
                f"Phone: {_value(item.phone)}",
                f"Email: {_value(item.email)}",
                f"Movie Name: {_value(item.movie_name)}",
                f"TMDB ID: {_value(item.tmdb_id)}",
                f"Movie URL: {_value(item.movie_url)}",
                f"Expected OTT Platform: {_value(item.expected_ott_platform)}",
                f"Expected OTT Date: {_value(item.expected_ott_release_date)}",
                f"Evidence URL: {_value(item.evidence_url)}",
                f"Comment: {_value(item.comment, limit=600)}",
                f"Submitted Time: {_value(item.created_at, 'Unknown')}",
                f"Status: {_value(item.status, 'Unknown')}",
            ]
        lines.append(
            f"Admin URL: {settings.SITE_URL.rstrip('/')}/admin/contact-requests/{item.request_id}"
        )
        return "\n".join(lines)[:1900]

    def create(
        self,
        *,
        request_id: str,
        request_kind: str,
        notification_type: str,
        event_key: str,
    ) -> RequestNotificationDelivery:
        dedupe_key = f"{request_kind}:{request_id}:DISCORD:{event_key}"
        existing = (
            self.db.query(RequestNotificationDelivery)
            .filter_by(dedupe_key=dedupe_key)
            .first()
        )
        if existing:
            return existing
        configured = NotificationService.discord_method()["configured"]
        record = RequestNotificationDelivery(
            request_id=request_id,
            request_kind=request_kind,
            notification_type=notification_type,
            provider="DISCORD",
            dedupe_key=dedupe_key,
            status="PENDING" if configured else "NOT_CONFIGURED",
        )
        self.db.add(record)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return (
                self.db.query(RequestNotificationDelivery)
                .filter_by(dedupe_key=dedupe_key)
                .one()
            )
        self._sync_contact_status(record)
        self.db.commit()
        return record

    def enqueue(self, record: RequestNotificationDelivery) -> RequestNotificationDelivery:
        if record.status == "NOT_CONFIGURED":
            return record
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task(
                "notifications.request_discord",
                args=[record.id],
                ignore_result=True,
            )
        except Exception as exc:
            record.status = "FAILED"
            record.sanitized_error = sanitize_error(exc)
            self._sync_contact_status(record)
            self.db.commit()
        return record

    def schedule_submission(self, item: MovieRequest | ContactRequest) -> RequestNotificationDelivery:
        movie = isinstance(item, MovieRequest)
        record = self.create(
            request_id=item.request_id,
            request_kind="MOVIE_REQUEST" if movie else "CONTACT_REQUEST",
            notification_type="MOVIE_REQUEST_SUBMITTED" if movie else f"{item.request_type}_SUBMITTED",
            event_key="SUBMITTED",
        )
        return self.enqueue(record)

    def schedule_ott_update(self, item: MovieRequest, event_key: str) -> RequestNotificationDelivery:
        record = self.create(
            request_id=item.request_id,
            request_kind="MOVIE_REQUEST",
            notification_type="MOVIE_REQUEST_OTT_UPDATE",
            event_key=f"OTT_UPDATE:{event_key}",
        )
        return self.enqueue(record)

    def schedule_admin_resend(
        self, item: MovieRequest | ContactRequest
    ) -> RequestNotificationDelivery:
        movie = isinstance(item, MovieRequest)
        record = self.create(
            request_id=item.request_id,
            request_kind="MOVIE_REQUEST" if movie else "CONTACT_REQUEST",
            notification_type="ADMIN_RESEND",
            event_key=f"ADMIN_RESEND:{uuid4()}",
        )
        return self.enqueue(record)

    def deliver(self, delivery_id: int) -> dict:
        record = self.db.get(RequestNotificationDelivery, delivery_id)
        if not record:
            return {"status": "MISSING", "delivery_id": delivery_id}
        if record.status == "SENT":
            return {"status": "SENT", "delivery_id": record.id, "deduplicated": True}
        if not NotificationService.discord_method()["configured"]:
            record.status = "NOT_CONFIGURED"
            record.sanitized_error = None
            self._sync_contact_status(record)
            self.db.commit()
            return {"status": record.status, "delivery_id": record.id}
        now = datetime.now(timezone.utc)
        record.attempt_count = (record.attempt_count or 0) + 1
        record.attempted_at = now
        item = self._request(record)
        if not item:
            record.status = "FAILED"
            record.sanitized_error = "Request record no longer exists"
            self.db.commit()
            return {"status": record.status, "delivery_id": record.id}
        message = (
            self.movie_message(item, update=record.notification_type == "MOVIE_REQUEST_OTT_UPDATE")
            if record.request_kind == "MOVIE_REQUEST"
            else self.contact_message(item)
        )
        try:
            NotificationService(self.db)._discord(message)
        except Exception as exc:
            safe_error = sanitize_error(exc)
            record.status = "FAILED"
            record.sanitized_error = safe_error
            self._sync_contact_status(record)
            self.db.commit()
            raise DiscordDeliveryError(safe_error) from exc
        record.status = "SENT"
        record.sent_at = now
        record.sanitized_error = None
        self.db.add(
            NotificationLog(
                fingerprint=record.dedupe_key,
                channel="discord",
                severity="info",
                message=message,
                last_notified_at=now,
            )
        )
        self._sync_contact_status(record)
        self.db.commit()
        return {"status": record.status, "delivery_id": record.id}

    def recover(self, limit: int = 100) -> list[int]:
        if not NotificationService.discord_method()["configured"]:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        rows = (
            self.db.query(RequestNotificationDelivery)
            .filter(
                RequestNotificationDelivery.provider == "DISCORD",
                RequestNotificationDelivery.status.in_({"PENDING", "FAILED", "NOT_CONFIGURED"}),
                RequestNotificationDelivery.attempt_count < 6,
                or_(
                    RequestNotificationDelivery.attempted_at.is_(None),
                    RequestNotificationDelivery.attempted_at < cutoff,
                ),
            )
            .order_by(RequestNotificationDelivery.created_at.asc())
            .limit(limit)
            .all()
        )
        queued = []
        for row in rows:
            row.status = "PENDING"
            row.sanitized_error = None
            self.db.commit()
            self.enqueue(row)
            queued.append(row.id)
        return queued

    def _request(self, record: RequestNotificationDelivery):
        model = MovieRequest if record.request_kind == "MOVIE_REQUEST" else ContactRequest
        return self.db.query(model).filter_by(request_id=record.request_id).first()

    def _sync_contact_status(self, record: RequestNotificationDelivery) -> None:
        if record.request_kind != "CONTACT_REQUEST":
            return
        item = self.db.query(ContactRequest).filter_by(request_id=record.request_id).first()
        if item:
            item.discord_status = record.status


def serialize_delivery(row: RequestNotificationDelivery) -> dict:
    return {
        "request_id": row.request_id,
        "notification_type": row.notification_type,
        "provider": row.provider,
        "attempted_at": row.attempted_at,
        "sent_at": row.sent_at,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "sanitized_error": row.sanitized_error,
        "created_at": row.created_at,
    }
