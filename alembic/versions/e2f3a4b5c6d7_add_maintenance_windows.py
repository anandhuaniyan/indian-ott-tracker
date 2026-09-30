"""Add administrator-managed maintenance windows.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="SCHEDULED", nullable=False),
        sa.Column("affected_services", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("impact_level", sa.String(length=20), server_default="LOW", nullable=False),
        sa.Column("impact_message", sa.String(length=500), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maintenance_windows_start_time", "maintenance_windows", ["start_time"])
    op.create_index("ix_maintenance_windows_end_time", "maintenance_windows", ["end_time"])
    op.create_index("ix_maintenance_windows_status", "maintenance_windows", ["status"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_windows_status", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_end_time", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_start_time", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
