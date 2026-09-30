"""Widen operation status for explicit provider quota states.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

from alembic import op
import sqlalchemy as sa


revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "operation_states",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "operation_states",
        "status",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
