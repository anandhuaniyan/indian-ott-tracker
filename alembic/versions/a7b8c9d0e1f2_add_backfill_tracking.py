"""Add persistent accelerated backfill tracking and person details.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operation_states", sa.Column("total_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("operation_states", sa.Column("status", sa.String(20), server_default="IDLE", nullable=False))
    op.add_column("operation_states", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_operation_states_status", "operation_states", ["status"])

    op.add_column("people", sa.Column("biography", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("birthday", sa.Date(), nullable=True))
    op.add_column("people", sa.Column("place_of_birth", sa.String(500), nullable=True))
    op.add_column("people", sa.Column("imdb_id", sa.String(32), nullable=True))
    op.create_index("ix_people_imdb_id", "people", ["imdb_id"])

    op.create_table(
        "backfill_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("operation", "entity_type", "entity_id", name="uq_backfill_entity"),
    )
    op.create_index("ix_backfill_records_operation", "backfill_records", ["operation"])
    op.create_index("ix_backfill_records_entity_id", "backfill_records", ["entity_id"])
    op.create_index("ix_backfill_records_status", "backfill_records", ["status"])


def downgrade() -> None:
    op.drop_index("ix_backfill_records_status", table_name="backfill_records")
    op.drop_index("ix_backfill_records_entity_id", table_name="backfill_records")
    op.drop_index("ix_backfill_records_operation", table_name="backfill_records")
    op.drop_table("backfill_records")
    op.drop_index("ix_people_imdb_id", table_name="people")
    op.drop_column("people", "imdb_id")
    op.drop_column("people", "place_of_birth")
    op.drop_column("people", "birthday")
    op.drop_column("people", "biography")
    op.drop_index("ix_operation_states_status", table_name="operation_states")
    op.drop_column("operation_states", "completed_at")
    op.drop_column("operation_states", "status")
    op.drop_column("operation_states", "total_count")
