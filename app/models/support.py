"""Private Support eligibility, contributions, audit, and Discord idempotency."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


CONTRIBUTION_STATUSES = ("PENDING", "CONFIRMED", "FAILED", "REFUNDED", "CANCELLED")
SUPPORT_AUDIT_SOURCES = ("WEB_ADMIN", "DISCORD_ADMIN", "SYSTEM")


class SupportProfile(TimestampMixin, Base):
    __tablename__ = "support_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled_by: Mapped[str | None] = mapped_column(String(200))


class SupportContribution(TimestampMixin, Base):
    __tablename__ = "support_contributions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_support_contribution_amount_positive"),
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'FAILED', 'REFUNDED', 'CANCELLED')",
            name="ck_support_contribution_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    payment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payment_method: Mapped[str] = mapped_column(String(80), nullable=False, default="PayNow")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    reference: Mapped[str | None] = mapped_column(String(200))
    admin_note: Mapped[str | None] = mapped_column(Text)


class SupportAuditLog(TimestampMixin, Base):
    __tablename__ = "support_audit_logs"
    __table_args__ = (
        CheckConstraint(
            "source IN ('WEB_ADMIN', 'DISCORD_ADMIN', 'SYSTEM')",
            name="ck_support_audit_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contribution_id: Mapped[int | None] = mapped_column(
        ForeignKey("support_contributions.id", ondelete="SET NULL"), index=True
    )
    before_value: Mapped[dict | None] = mapped_column(JSON)
    after_value: Mapped[dict | None] = mapped_column(JSON)


class DiscordInteractionReceipt(TimestampMixin, Base):
    __tablename__ = "discord_interaction_receipts"

    interaction_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
