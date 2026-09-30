"""Secure single-use account token issuance, validation, and email delivery."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from sqlalchemy.orm import Session

from app.models.community_rating import User, UserAuthToken
from app.services.email_layout import (
    absolute_site_url,
    build_transactional_email,
    deliver_email,
    smtp_configured,
)


VERIFY_EMAIL = "VERIFY_EMAIL"
PASSWORD_RESET = "PASSWORD_RESET"
TOKEN_TTLS = {VERIFY_EMAIL: timedelta(hours=24), PASSWORD_RESET: timedelta(minutes=45)}


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_token(db: Session, user: User, purpose: str) -> str:
    if purpose not in TOKEN_TTLS:
        raise ValueError("Unsupported account token purpose")
    now = datetime.now(timezone.utc)
    db.query(UserAuthToken).filter(
        UserAuthToken.user_id == user.id,
        UserAuthToken.purpose == purpose,
        UserAuthToken.used_at.is_(None),
    ).update({UserAuthToken.used_at: now}, synchronize_session=False)
    raw = secrets.token_urlsafe(32)
    db.add(
        UserAuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=token_digest(raw),
            expires_at=now + TOKEN_TTLS[purpose],
        )
    )
    db.commit()
    return raw


def consume_token(db: Session, raw_token: str, purpose: str) -> tuple[UserAuthToken, User] | None:
    if not raw_token or len(raw_token) > 256:
        return None
    now = datetime.now(timezone.utc)
    row = (
        db.query(UserAuthToken)
        .filter(
            UserAuthToken.token_hash == token_digest(raw_token),
            UserAuthToken.purpose == purpose,
        )
        .with_for_update()
        .first()
    )
    if not row or row.used_at is not None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return None
    user = db.get(User, row.user_id)
    return (row, user) if user and user.is_active else None


class AccountEmailService:
    @staticmethod
    def verification(user: User, raw_token: str) -> str:
        if not smtp_configured():
            return "NOT_CONFIGURED"
        url = absolute_site_url(f"/verify-email?token={raw_token}")
        message = build_transactional_email(
            to=user.email,
            subject="Verify your OTT Tracker email",
            heading="Verify your email",
            intro="Confirm this email address to finish setting up your OTT Tracker account.",
            plain=f"Verify your OTT Tracker email:\n\n{url}\n\nThis link expires in 24 hours.",
            details=[("Expires", "24 hours")],
            action_label="Verify email",
            action_url=url,
        )
        deliver_email(message)
        return "SENT"

    @staticmethod
    def password_reset(user: User, raw_token: str) -> str:
        if not smtp_configured():
            return "NOT_CONFIGURED"
        url = absolute_site_url(f"/reset-password?token={raw_token}")
        message = build_transactional_email(
            to=user.email,
            subject="Reset your OTT Tracker password",
            heading="Reset your password",
            intro="Use this single-use link to choose a new OTT Tracker password.",
            plain=f"Reset your OTT Tracker password:\n\n{url}\n\nThis link expires in 45 minutes.",
            details=[("Expires", "45 minutes")],
            action_label="Reset password",
            action_url=url,
        )
        deliver_email(message)
        return "SENT"
