"""Pinterest publishing models."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin


class PinterestBoard(TimestampMixin, Base):
    """A Pinterest board linked to the OTTTracker account."""

    __tablename__ = "pinterest_boards"

    id: Mapped[int] = mapped_column(primary_key=True)
    pinterest_board_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(500))
    language_code: Mapped[str | None] = mapped_column(String(10), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pins: Mapped[list["PinterestPin"]] = relationship(back_populates="board")


class PinterestPin(TimestampMixin, Base):
    """A generated or published Pinterest pin for a movie."""

    __tablename__ = "pinterest_pins"
    __table_args__ = (
        UniqueConstraint(
            "movie_id",
            "pin_type",
            "platform",
            name="uq_pinterest_pin_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    board_id: Mapped[int | None] = mapped_column(
        ForeignKey("pinterest_boards.id", ondelete="SET NULL"),
        index=True,
    )

    # Pin content
    pin_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )  # NOW_STREAMING, OTT_RELEASE, COMING_SOON, MOVIE_DISCOVERY, HIGHLY_RATED, THIS_WEEK_ON_OTT
    template: Mapped[str] = mapped_column(String(32), nullable=False)
    pin_title: Mapped[str] = mapped_column(String(200), nullable=False)
    pin_description: Mapped[str] = mapped_column(Text, nullable=False)
    destination_url: Mapped[str] = mapped_column(String(2000), nullable=False)

    # Image
    image_path: Mapped[str | None] = mapped_column(String(1000))
    image_width: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    image_height: Mapped[int] = mapped_column(Integer, default=1500, nullable=False)

    # Artwork rights
    artwork_source: Mapped[str | None] = mapped_column(String(50))  # poster, backdrop, branded_only
    artwork_rights_status: Mapped[str] = mapped_column(
        String(32),
        default="RIGHTS_UNKNOWN",
        nullable=False,
        index=True,
    )  # ARTWORK_APPROVED, ARTWORK_RIGHTS_UNKNOWN, BRANDED_ONLY

    # Pinterest result
    pinterest_pin_id: Mapped[str | None] = mapped_column(String(100), index=True)
    pinterest_pin_url: Mapped[str | None] = mapped_column(String(500))
    platform: Mapped[str | None] = mapped_column(String(100), index=True)  # OTT platform name

    # Status
    status: Mapped[str] = mapped_column(
        String(32),
        default="PENDING",
        nullable=False,
        index=True,
    )  # PENDING, ARTWORK_CHECK, GENERATING, READY_FOR_REVIEW, APPROVED, PUBLISHING, PUBLISHED, FAILED, SKIPPED, RIGHTS_REVIEW

    # Tracking
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Movie relationship
    movie: Mapped["Movie"] = relationship()
    board: Mapped["PinterestBoard | None"] = relationship(back_populates="pins")


class PinterestOAuthToken(TimestampMixin, Base):
    """Stored Pinterest OAuth tokens (never committed to Git)."""

    __tablename__ = "pinterest_oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str] = mapped_column(String(50), default="bearer", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(String(500))
    pinterest_user_id: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
