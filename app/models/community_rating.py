"""Community users and their movie ratings."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class User(TimestampMixin, Base):
    """An OTT Tracker website identity with server-owned authorization."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="USER", nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_session_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserAuthToken(TimestampMixin, Base):
    """Single-use, hashed account verification or password-reset token."""

    __tablename__ = "user_auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('VERIFY_EMAIL', 'PASSWORD_RESET')",
            name="ck_user_auth_token_purpose",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommunityRating(TimestampMixin, Base):
    __tablename__ = "community_ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "movie_id", name="uq_community_rating_user_movie"),
        CheckConstraint("rating >= 1 AND rating <= 10", name="ck_community_rating_range"),
        CheckConstraint("rating * 2 = CAST(rating * 2 AS INTEGER)", name="ck_community_rating_half_step"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False)
