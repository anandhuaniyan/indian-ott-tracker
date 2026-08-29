"""Scrub credentials from provider errors persisted before central redaction.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from alembic import context, op
import sqlalchemy as sa

from app.core.secrets import sanitize_error


revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


_TEXT_COLUMNS = {
    "operation_states": ("last_error",),
    "backfill_records": ("last_error",),
    "movie_ratings": ("last_error",),
    "movie_requests": (
        "confirmation_email_last_error",
        "completion_email_last_error",
        "rejection_email_last_error",
    ),
    "notification_logs": ("message",),
    "data_quality_issues": ("detail",),
    "ott_evidence": ("notes", "summary", "source_url"),
}


def upgrade() -> None:
    # The scrub is data-dependent and therefore has no static offline SQL.
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    for table, columns in _TEXT_COLUMNS.items():
        for column in columns:
            rows = bind.execute(
                sa.text(f'SELECT id, "{column}" AS value FROM "{table}" WHERE "{column}" IS NOT NULL')
            ).mappings()
            for row in rows:
                cleaned = sanitize_error(row["value"])
                if cleaned != row["value"]:
                    bind.execute(
                        sa.text(f'UPDATE "{table}" SET "{column}" = :value WHERE id = :id'),
                        {"value": cleaned, "id": row["id"]},
                    )


def downgrade() -> None:
    # Credential removal is intentionally irreversible.
    pass
