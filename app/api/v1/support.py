"""Private Support user, website-admin, and Discord-admin API."""

from datetime import datetime
from decimal import Decimal
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.rate_limit import check_limit, limit
from app.core.session_auth import require_admin_session, require_same_origin
from app.core.user_auth import require_user, require_user_same_origin
from app.database.connection import get_db
from app.models.community_rating import User
from app.models.support import (
    DiscordInteractionReceipt,
    SupportAuditLog,
    SupportContribution,
    SupportProfile,
)
from app.services.support import SupportService, contribution_payload


router = APIRouter(prefix="/api/v1/support", tags=["Private Support"])
admin_router = APIRouter(prefix="/api/v1/admin/support", tags=["Admin Support"])


class EligibilitySubmission(BaseModel):
    enabled: bool


class ContributionSubmission(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="SGD", min_length=3, max_length=3)
    payment_date: datetime | None = None
    payment_method: str = Field(default="PayNow", min_length=1, max_length=80)
    status: str = Field(default="PENDING", min_length=3, max_length=20)
    reference: str | None = Field(default=None, max_length=200)
    admin_note: str | None = Field(default=None, max_length=4000)


class ContributionUpdate(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_date: datetime | None = None
    payment_method: str | None = Field(default=None, min_length=1, max_length=80)
    status: str | None = Field(default=None, min_length=3, max_length=20)
    reference: str | None = Field(default=None, max_length=200)
    admin_note: str | None = Field(default=None, max_length=4000)


class DiscordAction(BaseModel):
    interaction_id: str = Field(min_length=2, max_length=120)
    actor_id: str = Field(min_length=1, max_length=100)
    actor_role_ids: list[str] = Field(default_factory=list, max_length=50)
    action: str = Field(min_length=2, max_length=80)
    email: str | None = Field(default=None, max_length=320)
    contribution_reference: str | None = Field(default=None, max_length=36)
    enabled: bool | None = None
    contribution: ContributionSubmission | None = None
    changes: ContributionUpdate | None = None


def _profile_payload(db: Session, user: User) -> dict:
    profile = SupportService(db).require_eligible(user)
    last = (
        db.query(SupportContribution)
        .filter(SupportContribution.user_id == user.id)
        .order_by(SupportContribution.payment_date.desc(), SupportContribution.id.desc())
        .first()
    )
    return {
        "enabled": profile.enabled,
        "last_contribution": contribution_payload(last) if last else None,
    }


def _safe_admin_actor(value: str) -> str:
    return value if value.startswith("user:") else "legacy-admin"


@router.get("/me")
def support_me(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _profile_payload(db, user)


@router.get("/history")
def support_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    SupportService(db).require_eligible(user)
    query = db.query(SupportContribution).filter(SupportContribution.user_id == user.id)
    total = query.count()
    rows = (
        query.order_by(SupportContribution.payment_date.desc(), SupportContribution.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": [contribution_payload(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/paynow")
def paynow(user: User = Depends(require_user), db: Session = Depends(get_db)):
    SupportService(db).require_eligible(user)
    if not settings.PAYNOW_QR_URL or not settings.PAYNOW_INSTRUCTIONS:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PayNow is not configured")
    return {
        "qr_url": settings.PAYNOW_QR_URL,
        "instructions": settings.PAYNOW_INSTRUCTIONS,
        "recipient_label": settings.PAYNOW_RECIPIENT_LABEL or None,
    }


@admin_router.get("/overview")
def admin_overview(
    search: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_session),
):
    contribution_counts = (
        db.query(
            SupportContribution.user_id.label("user_id"),
            func.count(SupportContribution.id).label("contribution_count"),
        )
        .group_by(SupportContribution.user_id)
        .subquery()
    )
    query = (
        db.query(
            User,
            SupportProfile,
            func.coalesce(contribution_counts.c.contribution_count, 0),
        )
        .outerjoin(SupportProfile, SupportProfile.user_id == User.id)
        .outerjoin(contribution_counts, contribution_counts.c.user_id == User.id)
    )
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(User.email.ilike(term), User.display_name.ilike(term)))
    total = query.count()
    rows = query.order_by(User.email).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "user_id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "email_verified": user.email_verified_at is not None,
                "support_enabled": bool(profile and profile.enabled),
                "contribution_count": contribution_count,
            }
            for user, profile, contribution_count in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@admin_router.post("/users/{user_id}/eligibility", dependencies=[Depends(require_same_origin)])
def admin_eligibility(
    user_id: int,
    payload: EligibilitySubmission,
    db: Session = Depends(get_db),
    actor: str = Depends(require_admin_session),
):
    user = SupportService(db).require_user(user_id)
    profile = SupportService(db).set_eligibility(user, payload.enabled, actor=_safe_admin_actor(actor), source="WEB_ADMIN")
    return {"user_id": user.id, "enabled": profile.enabled}


@admin_router.get("/users/{user_id}")
def admin_user_detail(
    user_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_session),
):
    service = SupportService(db)
    user = service.require_user(user_id)
    profile = db.get(SupportProfile, user.id)
    contributions = db.query(SupportContribution).filter_by(user_id=user.id).order_by(SupportContribution.payment_date.desc()).limit(200).all()
    audits = db.query(SupportAuditLog).filter_by(target_user_id=user.id).order_by(SupportAuditLog.created_at.desc()).limit(200).all()
    return {
        "user": {"user_id": user.id, "email": user.email, "display_name": user.display_name},
        "support_enabled": bool(profile and profile.enabled),
        "contributions": [contribution_payload(row, admin=True) for row in contributions],
        "audit": [
            {"action": row.action, "actor": row.actor, "source": row.source, "before": row.before_value, "after": row.after_value, "created_at": row.created_at}
            for row in audits
        ],
    }


@admin_router.post("/users/{user_id}/contributions", dependencies=[Depends(require_same_origin)])
def admin_add_contribution(
    user_id: int,
    payload: ContributionSubmission,
    db: Session = Depends(get_db),
    actor: str = Depends(require_admin_session),
):
    user = SupportService(db).require_user(user_id)
    values = payload.model_dump()
    values["contribution_status"] = values.pop("status")
    row = SupportService(db).add_contribution(user, **values, actor=_safe_admin_actor(actor), source="WEB_ADMIN")
    return contribution_payload(row, admin=True)


@admin_router.patch("/contributions/{public_id}", dependencies=[Depends(require_same_origin)])
def admin_update_contribution(
    public_id: str,
    payload: ContributionUpdate,
    db: Session = Depends(get_db),
    actor: str = Depends(require_admin_session),
):
    row = db.query(SupportContribution).filter_by(public_id=public_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contribution not found")
    row = SupportService(db).update_contribution(row, payload.model_dump(exclude_unset=True), actor=_safe_admin_actor(actor), source="WEB_ADMIN")
    return contribution_payload(row, admin=True)


def _discord_authorized(secret: str | None, actor_id: str, role_ids: list[str]) -> bool:
    configured_secret = settings.SUPPORT_DISCORD_SHARED_SECRET
    if not configured_secret or not secret or not hmac.compare_digest(secret, configured_secret):
        return False
    admins = {item.strip() for item in settings.SUPPORT_DISCORD_ADMIN_IDS.split(",") if item.strip()}
    roles = {item.strip() for item in settings.SUPPORT_DISCORD_ADMIN_ROLE_IDS.split(",") if item.strip()}
    return actor_id in admins or bool(roles.intersection(role_ids))


@router.post("/discord/actions")
def discord_action(
    payload: DiscordAction,
    request: Request,
    x_discord_support_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    check_limit(request, "support-discord-auth-fail", 10, identity=client_ip)
    if not _discord_authorized(x_discord_support_secret, payload.actor_id, payload.actor_role_ids):
        limit(request, "support-discord-auth-fail", 10, 900, identity=client_ip)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Discord administrator authorization required")
    existing = db.get(DiscordInteractionReceipt, payload.interaction_id)
    if existing:
        return {**existing.result, "deduplicated": True}
    receipt = DiscordInteractionReceipt(
        interaction_id=payload.interaction_id,
        actor=f"discord:{payload.actor_id}",
        action=payload.action,
        result={"status": "PROCESSING"},
    )
    db.add(receipt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.get(DiscordInteractionReceipt, payload.interaction_id)
        return {**(existing.result if existing else {"status": "PROCESSING"}), "deduplicated": True}
    service = SupportService(db)
    user = db.query(User).filter(func.lower(User.email) == (payload.email or "").strip().casefold()).first()
    if payload.action in {"set_eligibility", "add_contribution", "status", "history"} and not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Existing OTT Tracker user not found")
    actor = f"discord:{payload.actor_id}"
    if payload.action == "set_eligibility" and payload.enabled is not None:
        profile = service.set_eligibility(user, payload.enabled, actor=actor, source="DISCORD_ADMIN")
        result = {"status": "OK", "enabled": profile.enabled, "email": user.email}
    elif payload.action == "add_contribution" and payload.contribution:
        values = payload.contribution.model_dump()
        values["contribution_status"] = values.pop("status")
        row = service.add_contribution(user, **values, actor=actor, source="DISCORD_ADMIN")
        result = {"status": "OK", "contribution": contribution_payload(row, admin=True)}
    elif payload.action == "update_contribution" and payload.contribution_reference and payload.changes:
        row = db.query(SupportContribution).filter_by(public_id=payload.contribution_reference).first()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contribution not found")
        row = service.update_contribution(row, payload.changes.model_dump(exclude_unset=True), actor=actor, source="DISCORD_ADMIN")
        result = {"status": "OK", "contribution": contribution_payload(row, admin=True)}
    elif payload.action == "status":
        profile = db.get(SupportProfile, user.id)
        result = {"status": "OK", "email": user.email, "enabled": bool(profile and profile.enabled)}
    elif payload.action == "history":
        rows = db.query(SupportContribution).filter_by(user_id=user.id).order_by(SupportContribution.payment_date.desc()).limit(20).all()
        result = {"status": "OK", "email": user.email, "contributions": [contribution_payload(row, admin=True) for row in rows]}
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported Discord Support action")
    result = jsonable_encoder(result)
    receipt.result = result
    db.commit()
    return result
