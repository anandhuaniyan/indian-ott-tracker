"""Single transactional business service for website and Discord Support admin."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.community_rating import User
from app.models.support import (
    CONTRIBUTION_STATUSES,
    SUPPORT_AUDIT_SOURCES,
    SupportAuditLog,
    SupportContribution,
    SupportProfile,
)


def contribution_payload(row: SupportContribution, *, admin: bool = False) -> dict:
    value = {
        "reference": row.public_id,
        "amount": f"{row.amount:.2f}",
        "currency": row.currency,
        "payment_date": row.payment_date,
        "payment_method": row.payment_method,
        "status": row.status,
    }
    if admin:
        value.update(
            {
                "user_id": row.user_id,
                "payment_reference": row.reference,
                "admin_note": row.admin_note,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    return value


def _audit_value(row: SupportContribution | None) -> dict | None:
    if not row:
        return None
    value = contribution_payload(row, admin=True)
    for key in ("payment_date", "created_at", "updated_at"):
        if value.get(key):
            value[key] = value[key].isoformat()
    return value


class SupportService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _source(value: str) -> str:
        value = value.upper()
        if value not in SUPPORT_AUDIT_SOURCES:
            raise ValueError("Invalid support audit source")
        return value

    @staticmethod
    def _money(value: Decimal | str) -> Decimal:
        try:
            amount = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid amount") from exc
        if amount <= 0 or amount > Decimal("9999999999.99"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid amount")
        return amount

    def require_user(self, user_id: int) -> User:
        user = self.db.get(User, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        return user

    def require_eligible(self, user: User) -> SupportProfile:
        profile = self.db.get(SupportProfile, user.id)
        if not profile or not profile.enabled:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access is not enabled")
        return profile

    def set_eligibility(
        self, user: User, enabled: bool, *, actor: str, source: str
    ) -> SupportProfile:
        source = self._source(source)
        profile = self.db.get(SupportProfile, user.id)
        before = {"enabled": bool(profile and profile.enabled)}
        if not profile:
            profile = SupportProfile(user_id=user.id)
            self.db.add(profile)
        profile.enabled = enabled
        profile.enabled_at = datetime.now(timezone.utc) if enabled else None
        profile.enabled_by = actor
        self.db.add(
            SupportAuditLog(
                action="SUPPORT_ENABLED" if enabled else "SUPPORT_DISABLED",
                actor=actor,
                source=source,
                target_user_id=user.id,
                before_value=before,
                after_value={"enabled": enabled},
            )
        )
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def add_contribution(
        self,
        user: User,
        *,
        amount: Decimal | str,
        currency: str = "SGD",
        payment_date: datetime | None = None,
        payment_method: str = "PayNow",
        contribution_status: str = "PENDING",
        reference: str | None = None,
        admin_note: str | None = None,
        actor: str,
        source: str,
    ) -> SupportContribution:
        source = self._source(source)
        contribution_status = contribution_status.upper()
        if contribution_status not in CONTRIBUTION_STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid contribution status")
        currency = currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid currency")
        row = SupportContribution(
            public_id=str(uuid4()),
            user_id=user.id,
            amount=self._money(amount),
            currency=currency,
            payment_date=payment_date or datetime.now(timezone.utc),
            payment_method=payment_method.strip()[:80] or "PayNow",
            status=contribution_status,
            reference=(reference or "").strip()[:200] or None,
            admin_note=(admin_note or "").strip()[:4000] or None,
        )
        self.db.add(row)
        self.db.flush()
        self.db.add(
            SupportAuditLog(
                action="CONTRIBUTION_CREATED",
                actor=actor,
                source=source,
                target_user_id=user.id,
                contribution_id=row.id,
                after_value=_audit_value(row),
            )
        )
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_contribution(
        self,
        row: SupportContribution,
        changes: dict,
        *,
        actor: str,
        source: str,
    ) -> SupportContribution:
        source = self._source(source)
        before = _audit_value(row)
        if "amount" in changes and changes["amount"] is not None:
            row.amount = self._money(changes["amount"])
        if "currency" in changes and changes["currency"] is not None:
            currency = str(changes["currency"]).strip().upper()
            if len(currency) != 3 or not currency.isalpha():
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid currency")
            row.currency = currency
        if "payment_date" in changes and changes["payment_date"] is not None:
            row.payment_date = changes["payment_date"]
        if "payment_method" in changes and changes["payment_method"] is not None:
            row.payment_method = str(changes["payment_method"]).strip()[:80]
        if "status" in changes and changes["status"] is not None:
            new_status = str(changes["status"]).upper()
            if new_status not in CONTRIBUTION_STATUSES:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid contribution status")
            row.status = new_status
        if "reference" in changes:
            row.reference = (str(changes["reference"] or "").strip()[:200] or None)
        if "admin_note" in changes:
            row.admin_note = (str(changes["admin_note"] or "").strip()[:4000] or None)
        self.db.add(
            SupportAuditLog(
                action="CONTRIBUTION_UPDATED",
                actor=actor,
                source=source,
                target_user_id=row.user_id,
                contribution_id=row.id,
                before_value=before,
                after_value=_audit_value(row),
            )
        )
        self.db.commit()
        self.db.refresh(row)
        return row
