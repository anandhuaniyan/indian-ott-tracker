"""Add private contact requests and movie-request phone details.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movie_requests", sa.Column("whatsapp_phone", sa.String(length=50), nullable=True))
    op.create_table(
        "contact_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("request_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="NEW", nullable=False),
        sa.Column("name", sa.String(length=200)),
        sa.Column("whatsapp", sa.String(length=50)),
        sa.Column("phone", sa.String(length=50)),
        sa.Column("email", sa.String(length=320)),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("movie_name", sa.String(length=500)),
        sa.Column("movie_url", sa.String(length=1000)),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("issue_type", sa.String(length=100)),
        sa.Column("expected_ott_platform", sa.String(length=100)),
        sa.Column("expected_ott_release_date", sa.Date()),
        sa.Column("evidence_url", sa.String(length=1000)),
        sa.Column("local_movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="SET NULL")),
        sa.Column("ott_evidence_id", sa.Integer(), sa.ForeignKey("ott_evidence.id", ondelete="SET NULL")),
        sa.Column("discord_status", sa.String(length=30), server_default="PENDING", nullable=False),
        sa.Column("receipt_email_status", sa.String(length=30), server_default="PENDING", nullable=False),
        sa.Column("outcome_email_status", sa.String(length=30)),
        sa.Column("admin_notes", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("access_approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_contact_requests_request_id"),
    )
    for column in ("request_id", "request_type", "status", "movie_name", "tmdb_id", "local_movie_id", "ott_evidence_id"):
        op.create_index(f"ix_contact_requests_{column}", "contact_requests", [column])


def downgrade() -> None:
    op.drop_table("contact_requests")
    op.drop_column("movie_requests", "whatsapp_phone")
