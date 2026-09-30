"""Conservative, auditable movie content-safety classification."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.content_safety import ContentSafetyAudit
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieReleaseDate
from app.models.operations import OperationState
from app.services.languages import language_name
from app.services.notification_service import NotificationService


CLASSIFICATIONS = {"UNKNOWN", "SAFE", "MATURE", "RESTRICTED", "BLOCKED"}
VISIBILITY = {
    "UNKNOWN": "FULL",
    "SAFE": "FULL",
    "MATURE": "FULL",
    "RESTRICTED": "DIRECT_ONLY",
    "BLOCKED": "HIDDEN",
}
EXPLICIT_CERTIFICATIONS = {"X", "XXX"}
MATURE_CERTIFICATIONS = {
    "A", "R", "NC-17", "18", "18+", "R18", "R18+", "M18", "TV-MA", "MA 15+"
}


class ContentSafetyService:
    operation = "content_safety.scan"

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _certification(movie: Movie) -> str | None:
        values = [row for row in movie.release_dates if (row.certification or "").strip()]
        if not values:
            return None
        preferred = next((row for row in values if row.country == "IN"), values[0])
        return preferred.certification.strip().upper()

    def classify(self, movie: Movie) -> dict:
        certification = self._certification(movie)
        if movie.adult:
            return {
                "classification": "BLOCKED",
                "risk_score": 100,
                "certification": certification,
                "reasons": ["TMDB adult flag"],
            }
        if certification in EXPLICIT_CERTIFICATIONS:
            return {
                "classification": "RESTRICTED",
                "risk_score": 85,
                "certification": certification,
                "reasons": [f"Explicit-content certification: {certification}"],
            }
        if certification in MATURE_CERTIFICATIONS:
            return {
                "classification": "MATURE",
                "risk_score": 50,
                "certification": certification,
                "reasons": [f"Mature audience certification: {certification}"],
            }
        return {
            "classification": "SAFE",
            "risk_score": 5 if certification else 0,
            "certification": certification,
            "reasons": [f"General-audience certification: {certification}"] if certification else ["No restrictive signal detected"],
        }

    def _imdb_id(self, movie: Movie) -> str | None:
        return next(
            (row.external_id for row in movie.external_ids if row.provider.lower() == "imdb"),
            None,
        )

    def _transition(
        self,
        movie: Movie,
        *,
        classification: str,
        risk_score: int,
        reasons: list[str],
        certification: str | None,
        source: str,
        action: str | None = None,
    ) -> ContentSafetyAudit | None:
        classification = classification.upper()
        source = source.upper()
        if classification not in CLASSIFICATIONS:
            raise ValueError("Unknown safety classification")
        if source not in {"AUTOMATIC", "MANUAL"}:
            raise ValueError("Unknown classification source")
        if source == "AUTOMATIC" and movie.safety_manual_override:
            return None

        previous = movie.safety_classification or "UNKNOWN"
        visibility_before = movie.safety_public_visibility or "FULL"
        visibility_after = VISIBILITY[classification]
        changed = previous != classification or visibility_before != visibility_after
        should_audit = (
            source == "MANUAL"
            or classification in {"RESTRICTED", "BLOCKED"}
            or previous in {"RESTRICTED", "BLOCKED"}
            or visibility_before != visibility_after
        ) and (changed or source == "MANUAL")

        movie.safety_classification = classification
        movie.safety_risk_score = max(0, min(100, int(risk_score)))
        movie.safety_reasons = [str(reason)[:500] for reason in reasons if str(reason).strip()]
        movie.safety_public_visibility = visibility_after
        movie.safety_classification_source = source
        movie.safety_classified_at = datetime.now(timezone.utc)
        movie.safety_manual_override = source == "MANUAL"
        if not should_audit:
            return None

        if not action:
            if previous in {"RESTRICTED", "BLOCKED"} and classification not in {"RESTRICTED", "BLOCKED"}:
                action = "RESTORED"
            elif classification == "BLOCKED":
                action = "HIDDEN_FROM_PUBLIC_CATALOGUE"
            elif classification == "RESTRICTED":
                action = "RESTRICTED_PROMOTIONAL_SURFACES"
            else:
                action = "MANUAL_OVERRIDE"
        audit = ContentSafetyAudit(
            movie_id=movie.id,
            tmdb_id=movie.tmdb_id,
            imdb_id=self._imdb_id(movie),
            title=movie.title,
            original_title=movie.original_title,
            release_date=movie.release_date,
            language=movie.original_language,
            previous_classification=previous,
            new_classification=classification,
            risk_score=movie.safety_risk_score,
            tmdb_adult=bool(movie.adult),
            certification=certification,
            reasons=movie.safety_reasons or [],
            classification_source=source,
            action=action,
            public_visibility_before=visibility_before,
            public_visibility_after=visibility_after,
        )
        self.db.add(audit)
        return audit

    def apply_automatic(self, movie: Movie, *, notify: bool = True) -> ContentSafetyAudit | None:
        result = self.classify(movie)
        audit = self._transition(movie, source="AUTOMATIC", **result)
        self.db.commit()
        if audit and notify:
            self.db.refresh(audit)
            if not self.notify_audit(audit.id):
                self._enqueue_notification("content_safety.notification", [audit.id])
        return audit

    def apply_manual(
        self, movie: Movie, classification: str, reason: str, risk_score: int | None = None
    ) -> ContentSafetyAudit:
        classification = classification.upper()
        defaults = {"SAFE": 0, "MATURE": 50, "RESTRICTED": 85, "BLOCKED": 100}
        audit = self._transition(
            movie,
            classification=classification,
            risk_score=defaults.get(classification, 0) if risk_score is None else risk_score,
            reasons=[reason or "Administrator override"],
            certification=self._certification(movie),
            source="MANUAL",
        )
        if not audit:
            raise ValueError("Manual safety action was not recorded")
        self.db.commit()
        self.db.refresh(audit)
        if self._requires_notification(audit):
            if not self.notify_audit(audit.id):
                self._enqueue_notification("content_safety.notification", [audit.id])
        else:
            audit.notification_status = "NOT_REQUIRED"
            self.db.commit()
        return audit

    @staticmethod
    def _requires_notification(audit: ContentSafetyAudit) -> bool:
        """Alert only when public visibility actually changed in an important way."""
        return (
            audit.public_visibility_before != audit.public_visibility_after
            and (
                audit.action == "RESTORED"
                or audit.new_classification in {"RESTRICTED", "BLOCKED"}
            )
        )

    @staticmethod
    def _enqueue_notification(task_name: str, args: list) -> None:
        if not NotificationService.discord_method()["configured"]:
            return
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task(task_name, args=args, ignore_result=True)
        except Exception:
            # The safety transition is already durable; the FAILED audit state
            # remains visible for the next bounded retry/recovery pass.
            return

    @staticmethod
    def _action_label(audit: ContentSafetyAudit) -> str:
        if audit.action == "RESTORED":
            return "RESTORED TO PUBLIC CATALOGUE"
        if audit.new_classification == "BLOCKED":
            return "BLOCKED FROM PUBLIC CATALOGUE"
        return "RESTRICTED FROM PROMOTIONAL SURFACES"

    def _message(self, audit: ContentSafetyAudit) -> str:
        reasons = "\n".join(f"• {reason}" for reason in (audit.reasons or [])) or "• Not supplied"
        admin_url = f"{settings.SITE_URL.rstrip('/')}/admin/content-safety?movie={audit.movie_id}"
        return (
            "OTTTracker Content Safety Alert\n\n"
            f"Action: {self._action_label(audit)}\nTitle: {audit.title}\n"
            f"Year: {audit.release_date.year if audit.release_date else 'Unknown'}\n"
            f"Language: {language_name(audit.language, audit.language or 'Unknown')}\n"
            f"TMDB ID: {audit.tmdb_id}\nCertification: {audit.certification or 'Unknown'}\n"
            f"TMDB Adult: {audit.tmdb_adult}\nRisk Score: {audit.risk_score}/100\n\n"
            f"Reason:\n{reasons}\n\nPrevious Status: {audit.previous_classification}\n"
            f"New Status: {audit.new_classification}\nPublic Visibility: {audit.public_visibility_after}\n"
            f"Source: {audit.classification_source.title()} safety classification\nAdmin: {admin_url}"
        )

    def notify_audit(self, audit_id: int) -> bool:
        audit = self.db.get(ContentSafetyAudit, audit_id)
        if not audit or audit.notification_status in {"SENT", "DIGEST_SENT"}:
            return bool(audit)
        if not self._requires_notification(audit):
            audit.notification_status = "NOT_REQUIRED"
            self.db.commit()
            return True
        audit.notification_attempts = (audit.notification_attempts or 0) + 1
        try:
            sent = NotificationService(self.db).notify(
                self._message(audit),
                severity="high",
                fingerprint=f"content-safety-transition:{audit.id}",
                cooldown_minutes=525600,
                channels=("discord",),
            )
            audit.notification_status = "SENT" if sent else (
                "FAILED" if NotificationService.discord_method()["configured"] else "NOT_CONFIGURED"
            )
            audit.notification_error = None if sent else "Discord notification was not delivered"
            audit.notified_at = datetime.now(timezone.utc) if sent else None
        except Exception as exc:
            self.db.rollback()
            audit = self.db.get(ContentSafetyAudit, audit_id)
            audit.notification_status = "FAILED"
            audit.notification_error = sanitize_error(exc)
        self.db.commit()
        return audit.notification_status == "SENT"

    def _digest_message(self, audits: list[ContentSafetyAudit], scanned: int) -> str:
        restricted = [row for row in audits if row.new_classification == "RESTRICTED"]
        blocked = [row for row in audits if row.new_classification == "BLOCKED"]
        lines = [
            "OTTTracker Content Safety Backfill",
            "",
            f"Scanned: {scanned}",
            f"New Restricted: {len(restricted)}",
            f"New Blocked: {len(blocked)}",
            "",
        ]
        for label, rows in (("BLOCKED", blocked), ("RESTRICTED", restricted)):
            if rows:
                lines.append(f"{label}:")
                lines.extend(
                    f"{index}. {row.title} — {language_name(row.language, row.language or 'Unknown')} — score {row.risk_score}"
                    for index, row in enumerate(rows[:15], 1)
                )
                if len(rows) > 15:
                    lines.append(f"…and {len(rows) - 15} more in Admin")
                lines.append("")
        lines.append(f"Review: {settings.SITE_URL.rstrip('/')}/admin/content-safety")
        return "\n".join(lines)[:1900]

    def notify_digest(self, audit_ids: list[int], scanned: int) -> bool:
        audits = self.db.query(ContentSafetyAudit).filter(ContentSafetyAudit.id.in_(audit_ids)).all()
        if not audits:
            return True
        sent = NotificationService(self.db).notify(
            self._digest_message(audits, scanned),
            severity="high",
            fingerprint="content-safety-backfill:" + ":".join(map(str, sorted(audit_ids))),
            cooldown_minutes=525600,
            channels=("discord",),
        )
        status = "DIGEST_SENT" if sent else (
            "FAILED" if NotificationService.discord_method()["configured"] else "NOT_CONFIGURED"
        )
        for audit in audits:
            audit.notification_status = status
            audit.notification_attempts = (audit.notification_attempts or 0) + 1
            audit.notification_error = None if sent else "Discord digest was not delivered"
            audit.notified_at = datetime.now(timezone.utc) if sent else None
        self.db.commit()
        return sent

    def scan_batch(self, batch_size: int = 500, *, bulk: bool = True) -> dict:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not state:
            state = OperationState(name=self.operation, status="IDLE", details={})
            self.db.add(state)
            self.db.flush()
        if not state.cursor:
            state.processed_count = 0
            state.completed_at = None
        rows = (
            self.db.query(Movie)
            .options(selectinload(Movie.external_ids), selectinload(Movie.release_dates))
            .filter(Movie.id > (state.cursor or 0))
            .order_by(Movie.id)
            .limit(max(1, min(batch_size, 2000)))
            .all()
        )
        if not rows:
            state.status = "COMPLETE"
            state.completed_at = datetime.now(timezone.utc)
            state.last_success_at = state.completed_at
            state.last_error = None
            state.cursor = 0
            self.db.commit()
            return {"operation": self.operation, "processed": 0, "restricted": 0, "blocked": 0, "complete": True}
        audits: list[ContentSafetyAudit] = []
        restricted = blocked = 0
        for movie in rows:
            result = self.classify(movie)
            audit = self._transition(movie, source="AUTOMATIC", **result)
            if audit:
                audits.append(audit)
                restricted += int(audit.new_classification == "RESTRICTED")
                blocked += int(audit.new_classification == "BLOCKED")
        complete = len(rows) < batch_size
        state.cursor = 0 if complete else rows[-1].id
        state.processed_count = (state.processed_count or 0) + len(rows)
        state.total_count = self.db.query(func.count(Movie.id)).scalar() or 0
        state.status = "COMPLETE" if complete else "RUNNING"
        state.completed_at = datetime.now(timezone.utc) if complete else None
        state.last_success_at = datetime.now(timezone.utc)
        state.last_error = None
        state.details = {"last_batch": len(rows), "restricted": restricted, "blocked": blocked}
        self.db.commit()
        audit_ids = [row.id for row in audits]
        if bulk and audit_ids:
            if not self.notify_digest(audit_ids, len(rows)):
                self._enqueue_notification(
                    "content_safety.digest_notification", [audit_ids, len(rows)]
                )
        elif not bulk:
            for audit_id in audit_ids:
                self.notify_audit(audit_id)
        return {
            "operation": self.operation,
            "processed": len(rows),
            "restricted": restricted,
            "blocked": blocked,
            "audit_ids": audit_ids,
            "complete": complete,
        }
