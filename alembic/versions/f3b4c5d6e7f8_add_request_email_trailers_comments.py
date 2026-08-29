"""Add request delivery state, trailers, and moderated comments.

Revision ID: f3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""

from alembic import op
import sqlalchemy as sa


revision = "f3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "movie_requests",
        sa.Column("movie_existed_at_submission", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    for kind in ("confirmation", "completion", "rejection"):
        op.add_column(
            "movie_requests",
            sa.Column(f"{kind}_email_attempt_count", sa.Integer(), server_default="0", nullable=False),
        )
    op.add_column(
        "movie_requests",
        sa.Column("admin_notification_email_status", sa.String(length=20), server_default="PENDING", nullable=False),
    )
    op.add_column("movie_requests", sa.Column("admin_notification_email_sent_at", sa.DateTime(timezone=True)))
    op.add_column("movie_requests", sa.Column("admin_notification_email_last_error", sa.Text()))
    op.add_column("movie_requests", sa.Column("admin_notification_email_last_attempt_at", sa.DateTime(timezone=True)))
    op.add_column(
        "movie_requests",
        sa.Column("admin_notification_email_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )

    # Multiple viewers may request the same title. Only repeated active requests
    # from the same normalized email address are deduplicated.
    op.drop_index("uq_movie_requests_active_external_id", table_name="movie_requests")
    op.create_index(
        "uq_movie_requests_active_email_external_id",
        "movie_requests",
        [sa.text("lower(email)"), "external_movie_id"],
        unique=True,
        postgresql_where=sa.text(
            "external_movie_id IS NOT NULL AND status IN ('PENDING', 'REVIEWING', 'FOUND')"
        ),
    )

    op.create_table(
        "movie_trailers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=30), server_default="YouTube", nullable=False),
        sa.Column("video_key", sa.String(length=32), nullable=False),
        sa.Column("video_type", sa.String(length=50)),
        sa.Column("name", sa.String(length=500)),
        sa.Column("official", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("language", sa.String(length=10)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("movie_id", "provider", "video_key", name="uq_movie_trailer_provider_key"),
    )
    op.create_index("ix_movie_trailers_movie_id", "movie_trailers", ["movie_id"])
    op.create_index("ix_movie_trailers_video_key", "movie_trailers", ["video_key"])
    op.create_index("ix_movie_trailers_is_primary", "movie_trailers", ["is_primary"])
    op.create_index("ix_movie_trailers_movie_primary", "movie_trailers", ["movie_id", "is_primary"])

    op.create_table(
        "movie_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=320)),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("ip_hash", sa.String(length=64)),
        sa.Column("moderated_at", sa.DateTime(timezone=True)),
        sa.Column("moderation_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_movie_comments_movie_id", "movie_comments", ["movie_id"])
    op.create_index("ix_movie_comments_status", "movie_comments", ["status"])
    op.create_index("ix_movie_comments_ip_hash", "movie_comments", ["ip_hash"])
    op.create_index("ix_movie_comments_movie_status_created", "movie_comments", ["movie_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_movie_comments_movie_status_created", table_name="movie_comments")
    op.drop_index("ix_movie_comments_ip_hash", table_name="movie_comments")
    op.drop_index("ix_movie_comments_status", table_name="movie_comments")
    op.drop_index("ix_movie_comments_movie_id", table_name="movie_comments")
    op.drop_table("movie_comments")
    op.drop_index("ix_movie_trailers_movie_primary", table_name="movie_trailers")
    op.drop_index("ix_movie_trailers_is_primary", table_name="movie_trailers")
    op.drop_index("ix_movie_trailers_video_key", table_name="movie_trailers")
    op.drop_index("ix_movie_trailers_movie_id", table_name="movie_trailers")
    op.drop_table("movie_trailers")
    op.drop_index("uq_movie_requests_active_email_external_id", table_name="movie_requests")
    op.create_index(
        "uq_movie_requests_active_external_id",
        "movie_requests",
        ["external_movie_id"],
        unique=True,
        postgresql_where=sa.text(
            "external_movie_id IS NOT NULL AND status IN ('PENDING', 'REVIEWING', 'FOUND')"
        ),
    )
    for column in (
        "admin_notification_email_attempt_count",
        "admin_notification_email_last_attempt_at",
        "admin_notification_email_last_error",
        "admin_notification_email_sent_at",
        "admin_notification_email_status",
        "rejection_email_attempt_count",
        "completion_email_attempt_count",
        "confirmation_email_attempt_count",
        "movie_existed_at_submission",
    ):
        op.drop_column("movie_requests", column)
