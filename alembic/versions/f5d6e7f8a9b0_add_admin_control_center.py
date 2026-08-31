"""Add admin audit history and normalized OTT adapter records.

Revision ID: f5d6e7f8a9b0
Revises: f4c5d6e7f8a9
"""

from alembic import op
import sqlalchemy as sa


revision = "f5d6e7f8a9b0"
down_revision = "f4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operation_states", sa.Column("details", sa.JSON()))
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(100)),
        sa.Column("summary", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_target_type", "admin_audit_logs", ["target_type"])
    op.create_index("ix_admin_audit_logs_target_id", "admin_audit_logs", ["target_id"])
    op.create_table(
        "ott_source_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_key", sa.String(160), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("original_title", sa.String(500)),
        sa.Column("platform", sa.String(100)),
        sa.Column("release_date", sa.Date()),
        sa.Column("language", sa.String(20)),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("status", sa.String(20), server_default="UNMATCHED", nullable=False),
        sa.Column("matched_movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="SET NULL")),
        sa.Column("match_reason", sa.String(500)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "external_key", name="uq_ott_source_release"),
    )
    for column in ("source", "title", "platform", "release_date", "language", "status", "matched_movie_id"):
        op.create_index(f"ix_ott_source_releases_{column}", "ott_source_releases", [column])


def downgrade() -> None:
    op.drop_table("ott_source_releases")
    op.drop_table("admin_audit_logs")
    op.drop_column("operation_states", "details")
