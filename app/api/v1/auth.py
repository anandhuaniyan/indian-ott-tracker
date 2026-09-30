"""Authentication endpoints for normal OTT Tracker website users."""

from datetime import datetime, timezone
import logging
import re

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.rate_limit import check_limit, clear_limit, limit
from app.core.user_auth import (
    USER_COOKIE,
    USER_SESSION_TTL_SECONDS,
    create_user_session,
    hash_password,
    require_user,
    require_user_same_origin,
    revoke_user_session,
    verify_user_password,
)
from app.core.session_auth import verify_password as verify_legacy_admin_password
from app.database.connection import get_db
from app.models.community_rating import User, UserAuthToken
from app.models.support import SupportProfile
from app.services.account_tokens import (
    AccountEmailService,
    PASSWORD_RESET,
    VERIFY_EMAIL,
    consume_token,
    issue_token,
)

router = APIRouter(prefix="/api/v1/auth", tags=["User authentication"])
_email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_password_hasher = PasswordHasher()
_dummy_password_hash = hash_password("constant-time-login-check-123")
_logger = logging.getLogger(__name__)
_generic_email_response = {
    "accepted": True,
    "message": "If the account is eligible, an email will be sent shortly.",
}


class RegisterSubmission(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not _email_pattern.fullmatch(value):
            raise ValueError("A valid email address is required")
        return value

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        if not any(char.isalpha() for char in value) or not any(
            char.isdigit() for char in value
        ):
            raise ValueError("Password must include a letter and a number")
        return value

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        if value and len(value) < 2:
            raise ValueError("Display name must contain at least 2 characters")
        return value


class LoginSubmission(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class EmailSubmission(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not _email_pattern.fullmatch(value):
            raise ValueError("A valid email address is required")
        return value


class TokenSubmission(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class ResetPasswordSubmission(TokenSubmission):
    password: str = Field(min_length=10, max_length=128)

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return RegisterSubmission.strong_password(value)


def _public_user(user: User, db: Session | None = None) -> dict:
    support_eligible = False
    if db is not None:
        profile = db.get(SupportProfile, user.id)
        support_eligible = bool(profile and profile.enabled)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "email_verified": user.email_verified_at is not None,
        "support_eligible": support_eligible,
    }


def _set_user_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        USER_COOKIE,
        create_user_session(user.id, user.auth_session_version),
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=USER_SESSION_TTL_SECONDS,
        path="/",
    )


def _send_account_email(kind: str, user: User, raw_token: str) -> str:
    try:
        return (
            AccountEmailService.verification(user, raw_token)
            if kind == VERIFY_EMAIL
            else AccountEmailService.password_reset(user, raw_token)
        )
    except Exception as exc:
        _logger.warning("Account email delivery failed (%s)", type(exc).__name__)
        return "FAILED"


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_user_same_origin)],
)
def register(
    payload: RegisterSubmission,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    limit(request, "user-register-ip", 5, 900, identity=client_ip)
    if db.query(User.id).filter(User.email == payload.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        is_active=True,
        role="USER",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        ) from exc
    db.refresh(user)
    raw_token = issue_token(db, user, VERIFY_EMAIL)
    email_status = _send_account_email(VERIFY_EMAIL, user, raw_token)
    return {
        **_public_user(user, db),
        "verification_required": True,
        "email_status": email_status,
    }


@router.post("/login", dependencies=[Depends(require_user_same_origin)])
def login(
    payload: LoginSubmission,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    normalized_email = payload.email.strip().casefold()
    client_ip = request.client.host if request.client else "unknown"
    check_limit(request, "user-login-ip-fail", 10, identity=client_ip)
    check_limit(request, "user-login-account-fail", 5, identity=normalized_email)
    user = db.query(User).filter(User.email == normalized_email).first()
    bootstrap_admin = (
        user is None
        and normalized_email == (settings.ADMIN_NOTIFICATION_EMAIL or "").strip().casefold()
        and verify_legacy_admin_password(payload.password)
    )
    if bootstrap_admin:
        user = User(
            email=normalized_email,
            password_hash=hash_password(payload.password),
            display_name="Administrator",
            is_active=True,
            role="ADMIN",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    password_ok = verify_user_password(
        user.password_hash if user else _dummy_password_hash, payload.password
    )
    if user is None or not user.is_active or not password_ok:
        limit(request, "user-login-ip-fail", 10, 900, identity=client_ip)
        limit(request, "user-login-account-fail", 5, 900, identity=normalized_email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user.email_verified_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email verification required")
    clear_limit(request, "user-login-account-fail", identity=normalized_email)
    if _password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()
    _set_user_cookie(response, user)
    return _public_user(user, db)


@router.post("/verify-email", dependencies=[Depends(require_user_same_origin)])
def verify_email(
    payload: TokenSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    limit(request, "verification-consume-ip", 20, 3600, identity=client_ip)
    consumed = consume_token(db, payload.token, VERIFY_EMAIL)
    if not consumed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verification link is invalid or expired")
    token, user = consumed
    now = datetime.now(timezone.utc)
    token.used_at = now
    user.email_verified_at = user.email_verified_at or now
    db.commit()
    return {"verified": True, "message": "Email verified. You can now log in."}


@router.post("/resend-verification", dependencies=[Depends(require_user_same_origin)])
def resend_verification(
    payload: EmailSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    limit(request, "verification-resend-ip", 5, 3600, identity=client_ip)
    limit(request, "verification-resend-account", 3, 3600, identity=payload.email)
    user = db.query(User).filter(User.email == payload.email, User.is_active.is_(True)).first()
    if user and user.email_verified_at is None:
        raw_token = issue_token(db, user, VERIFY_EMAIL)
        _send_account_email(VERIFY_EMAIL, user, raw_token)
    return _generic_email_response


@router.post("/forgot-password", dependencies=[Depends(require_user_same_origin)])
def forgot_password(
    payload: EmailSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    limit(request, "password-reset-ip", 5, 3600, identity=client_ip)
    limit(request, "password-reset-account", 3, 3600, identity=payload.email)
    user = db.query(User).filter(User.email == payload.email, User.is_active.is_(True)).first()
    if user and user.email_verified_at is not None:
        raw_token = issue_token(db, user, PASSWORD_RESET)
        _send_account_email(PASSWORD_RESET, user, raw_token)
    return _generic_email_response


@router.post("/reset-password", dependencies=[Depends(require_user_same_origin)])
def reset_password(
    payload: ResetPasswordSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    limit(request, "password-reset-consume-ip", 20, 3600, identity=client_ip)
    consumed = consume_token(db, payload.token, PASSWORD_RESET)
    if not consumed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reset link is invalid or expired")
    token, user = consumed
    now = datetime.now(timezone.utc)
    token.used_at = now
    user.password_hash = hash_password(payload.password)
    user.auth_session_version = (user.auth_session_version or 0) + 1
    db.query(UserAuthToken).filter(
        UserAuthToken.user_id == user.id,
        UserAuthToken.purpose == PASSWORD_RESET,
        UserAuthToken.used_at.is_(None),
    ).update({UserAuthToken.used_at: now}, synchronize_session=False)
    db.commit()
    return {"reset": True, "message": "Password updated. Log in with your new password."}


@router.post("/logout", dependencies=[Depends(require_user_same_origin)])
def logout(response: Response, request: Request):
    revoke_user_session(request.cookies.get(USER_COOKIE))
    response.delete_cookie(USER_COOKIE, path="/")
    return {"authenticated": False}


@router.get("/me")
def current_user(
    user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return _public_user(user, db)
