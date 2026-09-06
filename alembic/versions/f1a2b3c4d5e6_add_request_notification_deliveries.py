"""Add durable notification delivery history for private requests.

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("request_kind", sa.String(length=30), nullable=False),
        sa.Column("notification_type", sa.String(length=60), nullable=False),
        sa.Column("provider", sa.String(length=20), server_default="DISCORD", nullable=False),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("sanitized_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_request_notification_delivery_dedupe"),
    )
    for column in ("request_id", "request_kind", "notification_type", "provider", "dedupe_key", "status"):
        op.create_index(
            f"ix_request_notification_deliveries_{column}",
            "request_notification_deliveries",
            [column],
        )


def downgrade() -> None:
    op.drop_table("request_notification_deliveries")
