"""Verified movie-request delivery, reconciliation and SLA automation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
import smtplib

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.operations import MovieRequest
from app.services.notification_service import NotificationService


ACTIVE_REQUEST_STATUSES = ("PENDING", "REVIEWING", "FOUND")
EMAIL_KINDS = ("confirmation", "completion", "rejection", "admin_notification")
EMAIL_STATUSES = ("PENDING", "SENT", "FAILED", "NOT_CONFIGURED")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _safe_error(exc: Exception) -> str:
    return sanitize_error(f"{type(exc).__name__}: {exc}", limit=1000)


class MovieRequestEmailService:
    RETRY_COOLDOWN = timedelta(minutes=5)

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def configured(kind: str = "confirmation") -> bool:
        recipient_ready = bool(settings.ADMIN_NOTIFICATION_EMAIL) if kind == "admin_notification" else True
        return bool(settings.SMTP_HOST and settings.SMTP_FROM and recipient_ready)

    @staticmethod
    def _title(item: MovieRequest) -> str:
        return item.verified_title or item.movie_name

    def _message(self, item: MovieRequest, kind: str) -> EmailMessage:
        title = self._title(item)
        safe_title = escape(title)
        reference = escape(item.request_id)
        if kind == "confirmation":
            subject = f"Movie Request Received — {title}"
            plain = (
                f"Your request for {title} has been received. We’ll review your request and aim to make it "
                f"available within 48 hours. We’ll email you again once it becomes available.\n\n"
                f"Current status: Pending\nRequest reference: {item.request_id}\n"
                f"Website: {settings.SITE_URL.rstrip('/')}"
            )
            html = (
                f"<h2>Request received</h2><p>Your request for <strong>{safe_title}</strong> has been received.</p>"
                "<p>We’ll review your request and aim to make it available within 48 hours. "
                "We’ll email you again once it becomes available.</p>"
                "<p>Current status: <strong>Pending</strong></p>"
                f'<p><a href="{escape(settings.SITE_URL.rstrip("/"), quote=True)}">Visit Indian OTT Tracker</a></p>'
                f"<p><small>Request reference: {reference}</small></p>"
            )
        elif kind == "completion":
            if not item.local_movie_id:
                raise ValueError("A local movie is required for completion email")
            url = f"{settings.SITE_URL.rstrip('/')}/movies/{item.local_movie_id}"
            subject = f"Your Requested Movie Is Now Available — {title}"
            plain = f"Good news — {title} has been added and is now available.\n\nView Movie: {url}"
            html = (
                f"<h2>Good news</h2><p><strong>{safe_title}</strong> has been added and is now available.</p>"
                f'<p><a href="{escape(url, quote=True)}">View Movie</a></p>'
            )
        elif kind == "rejection":
            reason = (item.public_rejection_reason or "The movie cannot currently be added.").strip()
            subject = f"Update on Your Movie Request — {title}"
            plain = f"We’re sorry, but {title} cannot currently be added.\n\n{reason}\n\nRequest reference: {item.request_id}"
            html = (
                f"<h2>Request update</h2><p>We’re sorry, but <strong>{safe_title}</strong> cannot currently be added.</p>"
                f"<p>{escape(reason)}</p><p><small>Request reference: {reference}</small></p>"
            )
        elif kind == "admin_notification":
            admin_url = f"{settings.SITE_URL.rstrip('/')}/admin/requests"
            subject = f"New Movie Request — {title}"
            fields = [
                f"Movie title: {title}",
                f"External ID: {item.external_movie_id or 'Unavailable'}",
                f"IMDb ID: {item.imdb_id or 'Unavailable'}",
                f"Language: {item.verified_language_name or item.verified_original_language or item.language or 'Unavailable'}",
                f"Release date: {item.verified_release_date or item.release_year or 'Unavailable'}",
                f"Requester email: {item.email}",
                f"Request ID: {item.request_id}",
                f"Additional details: {item.details or 'None'}",
                f"Admin Requests: {admin_url}",
            ]
            plain = "\n".join(fields)
            html = (
                f"<h2>New movie request</h2><p><strong>{safe_title}</strong></p>"
                "<dl>"
                f"<dt>External ID</dt><dd>{item.external_movie_id or 'Unavailable'}</dd>"
                f"<dt>IMDb ID</dt><dd>{escape(item.imdb_id or 'Unavailable')}</dd>"
                f"<dt>Language</dt><dd>{escape(item.verified_language_name or item.verified_original_language or item.language or 'Unavailable')}</dd>"
                f"<dt>Release date</dt><dd>{item.verified_release_date or item.release_year or 'Unavailable'}</dd>"
                f"<dt>Requester</dt><dd>{escape(item.email)}</dd>"
                f"<dt>Request ID</dt><dd>{reference}</dd>"
                f"<dt>Additional details</dt><dd>{escape(item.details or 'None')}</dd>"
                "</dl>"
                f'<p><a href="{escape(admin_url, quote=True)}">Open Admin Requests</a></p>'
            )
        else:
            raise ValueError("Unknown movie-request email kind")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.SMTP_FROM
        message["To"] = settings.ADMIN_NOTIFICATION_EMAIL if kind == "admin_notification" else item.email
        message.set_content(plain)
        message.add_alternative(
            '<div style="max-width:600px;margin:auto;padding:20px;font-family:Arial,sans-serif;line-height:1.55">'
            f"{html}</div>",
            subtype="html",
        )
        return message

    @staticmethod
    def _deliver(message: EmailMessage) -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
            if settings.SMTP_USE_TLS:
                client.starttls()
            if settings.SMTP_USERNAME:
                client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            client.send_message(message)

    def send(self, item: MovieRequest, kind: str, *, respect_cooldown: bool = True) -> dict:
        if kind not in EMAIL_KINDS:
            raise ValueError("Unknown movie-request email kind")
        if kind == "completion":
            movie = self.db.get(Movie, item.local_movie_id) if item.local_movie_id else None
            if item.status != "ADDED" or not movie or movie.tmdb_id != item.external_movie_id:
                return {"kind": kind, "status": getattr(item, "completion_email_status"), "sent": False, "skipped": "movie_not_added"}
        if kind == "rejection" and item.status != "REJECTED":
            return {"kind": kind, "status": getattr(item, "rejection_email_status"), "sent": False, "skipped": "request_not_rejected"}
        status_name = f"{kind}_email_status"
        sent_name = f"{kind}_email_sent_at"
        error_name = f"{kind}_email_last_error"
        attempt_name = f"{kind}_email_last_attempt_at"
        attempt_count_name = f"{kind}_email_attempt_count"
        status = getattr(item, status_name)
        if status == "SENT":
            return {"kind": kind, "status": "SENT", "sent": False, "skipped": "already_sent"}
        now = datetime.now(timezone.utc)
        last_attempt = _aware(getattr(item, attempt_name))
        if respect_cooldown and last_attempt and last_attempt > now - self.RETRY_COOLDOWN:
            return {"kind": kind, "status": status, "sent": False, "skipped": "cooldown"}
        setattr(item, attempt_name, now)
        setattr(item, attempt_count_name, (getattr(item, attempt_count_name) or 0) + 1)
        if not self.configured(kind):
            setattr(item, status_name, "NOT_CONFIGURED")
            setattr(item, error_name, "SMTP is not configured")
            self.db.commit()
            return {"kind": kind, "status": "NOT_CONFIGURED", "sent": False}
        try:
            message = self._message(item, kind)
            self._deliver(message)
        except Exception as exc:
            setattr(item, status_name, "FAILED")
            setattr(item, error_name, _safe_error(exc))
            self.db.commit()
            return {"kind": kind, "status": "FAILED", "sent": False}
        setattr(item, status_name, "SENT")
        setattr(item, sent_name, now)
        setattr(item, error_name, None)
        self.db.commit()
        return {"kind": kind, "status": "SENT", "sent": True}


class MovieRequestAutomationService:
    def __init__(self, db: Session):
        self.db = db
        self.email = MovieRequestEmailService(db)

    def _complete(self, item: MovieRequest, movie: Movie, *, force: bool = False) -> bool:
        if item.external_movie_id != movie.tmdb_id:
            return False
        if item.movie_existed_at_submission and not force:
            return False
        changed = item.status != "ADDED" or item.local_movie_id != movie.id
        item.status = "ADDED"
        item.local_movie_id = movie.id
        self.db.commit()
        self.email.send(item, "completion")
        return changed

    def reconcile_for_movie(self, movie: Movie) -> int:
        requests = self.db.query(MovieRequest).filter(
            MovieRequest.external_movie_id == movie.tmdb_id,
            MovieRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            MovieRequest.movie_existed_at_submission.is_(False),
        ).all()
        return sum(int(self._complete(item, movie)) for item in requests)

    def reconcile(self, batch_size: int = 200) -> dict:
        rows = (
            self.db.query(MovieRequest, Movie)
            .join(Movie, Movie.tmdb_id == MovieRequest.external_movie_id)
            .filter(
                MovieRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                MovieRequest.movie_existed_at_submission.is_(False),
            )
            .order_by(MovieRequest.id)
            .limit(batch_size)
            .all()
        )
        completed = sum(int(self._complete(item, movie)) for item, movie in rows)
        return {"matched": len(rows), "completed": completed}

    def check_sla(self, now: datetime | None = None, batch_size: int = 200) -> dict:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=36)
        requests = (
            self.db.query(MovieRequest)
            .filter(
                MovieRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                MovieRequest.created_at <= cutoff,
            )
            .order_by(MovieRequest.created_at)
            .limit(batch_size)
            .all()
        )
        warning_count = escalation_count = 0
        notifier = NotificationService(self.db)
        for item in requests:
            created = _aware(item.created_at) or now
            age = now - created
            title = item.verified_title or item.movie_name
            if age >= timedelta(hours=36) and item.sla_36_notified_at is None:
                notifier.notify(
                    f"Movie request {item.request_id} for {title} is approaching the 48-hour target.",
                    "warning",
                    f"movie-request-sla-36:{item.request_id}",
                    cooldown_minutes=10 * 365 * 24 * 60,
                )
                item.sla_36_notified_at = now
                self.db.commit()
                warning_count += 1
            if age >= timedelta(hours=48) and item.sla_48_notified_at is None:
                notifier.notify(
                    f"Movie request {item.request_id} for {title} has passed the 48-hour target.",
                    "high",
                    f"movie-request-sla-48:{item.request_id}",
                    cooldown_minutes=10 * 365 * 24 * 60,
                )
                item.sla_48_notified_at = now
                self.db.commit()
                escalation_count += 1
        return {"checked": len(requests), "warnings": warning_count, "escalations": escalation_count}

    def maintain(self) -> dict:
        return {"reconciliation": self.reconcile(), "sla": self.check_sla()}
