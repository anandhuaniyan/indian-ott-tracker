"""Private contact-request notifications and durable requester email delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
import smtplib

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.operations import ContactRequest, RequestNotificationDelivery


TYPE_LABELS = {
    "WEBSITE_ISSUE": "Report Website Issue",
    "INCORRECT_MOVIE": "Incorrect Movie Information",
    "INCORRECT_OTT": "Incorrect OTT Information",
    "ACCESS_REQUEST": "Request Access",
    "OTHER": "Other Contact Request",
}

EMAIL_EVENTS = {"RECEIVED", "IN_PROGRESS", "RESOLVED", "APPROVED", "REJECTED"}


class ContactEmailDeliveryError(RuntimeError):
    """A sanitized transient error that Celery may retry safely."""


class ContactRequestNotificationService:
    RETRY_DELAY = timedelta(minutes=5)

    def __init__(self, db: Session):
        self.db = db

    def notify_submission(self, item: ContactRequest) -> dict:
        # Local import avoids a module cycle: Discord formatting uses TYPE_LABELS.
        from app.services.request_discord import RequestDiscordService

        discord = RequestDiscordService(self.db).schedule_submission(item)
        email = self.schedule_email(item, "RECEIVED")
        return {
            "discord": discord.status,
            "email": email.status if email else item.receipt_email_status,
        }

    @staticmethod
    def configured() -> bool:
        return bool(settings.SMTP_HOST and settings.SMTP_FROM)

    def _message(self, item: ContactRequest, event: str) -> EmailMessage:
        label = TYPE_LABELS.get(item.request_type, "Website request")
        title = {
            "RECEIVED": f"We received your {label.lower()}",
            "IN_PROGRESS": f"Your website request {item.request_id} is in progress",
            "RESOLVED": f"Your website request {item.request_id} was resolved",
            "APPROVED": f"Your access request {item.request_id} was approved",
            "REJECTED": f"Update for website request {item.request_id}",
        }[event]
        status_label = event.replace("_", " ").title()
        site_url = settings.SITE_URL.rstrip("/")
        plain = (
            f"Request: {item.request_id}\nType: {label}\nStatus: {status_label}\n\n"
            "Your submission is private and is reviewed by an administrator. "
            "An access request never grants access automatically.\n\n"
            f"Website: {site_url}"
        )
        html = (
            '<div style="max-width:600px;margin:auto;padding:20px;font-family:Arial,sans-serif;line-height:1.55">'
            f"<h2>{escape(status_label)}</h2>"
            f"<p><strong>Request:</strong> {escape(item.request_id)}</p>"
            f"<p><strong>Type:</strong> {escape(label)}</p>"
            "<p>Your submission is private and is reviewed by an administrator. "
            "An access request never grants access automatically.</p>"
            f'<p><a href="{escape(site_url, quote=True)}">Visit Indian OTT Tracker</a></p>'
            "</div>"
        )
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = settings.SMTP_FROM
        message["To"] = item.email
        message.set_content(plain)
        message.add_alternative(html, subtype="html")
        return message

    @staticmethod
    def _deliver_message(message: EmailMessage) -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
            if settings.SMTP_USE_TLS:
                client.starttls()
            if settings.SMTP_USERNAME:
                client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            client.send_message(message)

    def schedule_email(
        self, item: ContactRequest, event: str, *, force_enqueue: bool = False
    ) -> RequestNotificationDelivery | None:
        event = event.upper()
        if event not in EMAIL_EVENTS:
            raise ValueError("Unknown contact-request email event")
        if not item.email:
            self._sync_status(item, event, "NOT_SUPPLIED")
            self.db.commit()
            return None
        dedupe_key = f"CONTACT_REQUEST:{item.request_id}:EMAIL:{event}"
        record = (
            self.db.query(RequestNotificationDelivery)
            .filter_by(dedupe_key=dedupe_key)
            .first()
        )
        if not record:
            record = RequestNotificationDelivery(
                request_id=item.request_id,
                request_kind="CONTACT_REQUEST",
                notification_type=f"CONTACT_EMAIL_{event}",
                provider="EMAIL",
                dedupe_key=dedupe_key,
                status="PENDING" if self.configured() else "NOT_CONFIGURED",
            )
            self.db.add(record)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                record = (
                    self.db.query(RequestNotificationDelivery)
                    .filter_by(dedupe_key=dedupe_key)
                    .one()
                )
        self._sync_status(item, event, record.status)
        self.db.commit()
        if record.status != "SENT" and (record.status != "NOT_CONFIGURED" or force_enqueue):
            self.enqueue(record)
        return record

    def enqueue(self, record: RequestNotificationDelivery) -> RequestNotificationDelivery:
        if not self.configured():
            record.status = "NOT_CONFIGURED"
            item = self._request(record)
            if item:
                self._sync_status(item, self._event(record), record.status)
            self.db.commit()
            return record
        try:
            from app.workers.celery_app import celery_app

            record.status = "PENDING"
            record.sanitized_error = None
            self.db.commit()
            celery_app.send_task(
                "notifications.contact_request_email", args=[record.id], ignore_result=True
            )
        except Exception as exc:
            record.status = "FAILED"
            record.sanitized_error = sanitize_error(exc)
            item = self._request(record)
            if item:
                self._sync_status(item, self._event(record), record.status)
            self.db.commit()
        return record

    def deliver(self, delivery_id: int) -> dict:
        record = self.db.get(RequestNotificationDelivery, delivery_id)
        if not record or record.provider != "EMAIL" or record.request_kind != "CONTACT_REQUEST":
            return {"status": "MISSING", "delivery_id": delivery_id}
        if record.status == "SENT":
            return {"status": "SENT", "delivery_id": record.id, "deduplicated": True}
        item = self._request(record)
        if not item or not item.email:
            record.status = "FAILED"
            record.sanitized_error = "Contact request or recipient no longer exists"
            self.db.commit()
            return {"status": record.status, "delivery_id": record.id}
        event = self._event(record)
        now = datetime.now(timezone.utc)
        record.attempt_count = (record.attempt_count or 0) + 1
        record.attempted_at = now
        if not self.configured():
            record.status = "NOT_CONFIGURED"
            record.sanitized_error = "SMTP is not configured"
            self._sync_status(item, event, record.status)
            self.db.commit()
            return {"status": record.status, "delivery_id": record.id}
        try:
            self._deliver_message(self._message(item, event))
        except Exception as exc:
            safe_error = sanitize_error(exc)
            record.status = "FAILED"
            record.sanitized_error = safe_error
            self._sync_status(item, event, record.status)
            self.db.commit()
            raise ContactEmailDeliveryError(safe_error) from exc
        record.status = "SENT"
        record.sent_at = now
        record.sanitized_error = None
        self._sync_status(item, event, record.status)
        self.db.commit()
        return {"status": record.status, "delivery_id": record.id}

    def recover(self, limit: int = 100) -> list[int]:
        if not self.configured():
            return []
        cutoff = datetime.now(timezone.utc) - self.RETRY_DELAY
        rows = (
            self.db.query(RequestNotificationDelivery)
            .filter(
                RequestNotificationDelivery.request_kind == "CONTACT_REQUEST",
                RequestNotificationDelivery.provider == "EMAIL",
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
            self.enqueue(row)
            queued.append(row.id)
        return queued

    @staticmethod
    def _event(record: RequestNotificationDelivery) -> str:
        return record.notification_type.removeprefix("CONTACT_EMAIL_")

    def _request(self, record: RequestNotificationDelivery) -> ContactRequest | None:
        return (
            self.db.query(ContactRequest)
            .filter_by(request_id=record.request_id)
            .first()
        )

    @staticmethod
    def _sync_status(item: ContactRequest, event: str, status: str) -> None:
        if event == "RECEIVED":
            item.receipt_email_status = status
        else:
            item.outcome_email_status = status
