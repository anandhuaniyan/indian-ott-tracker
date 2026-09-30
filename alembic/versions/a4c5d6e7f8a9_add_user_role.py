"""Add server-owned website user role.

Revision ID: a4c5d6e7f8a9
Revises: f5b6c7d8e9f0
"""

from alembic import op
import sqlalchemy as sa


revision = "a4c5d6e7f8a9"
down_revision = "f5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), nullable=False, server_default="USER"),
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('USER', 'ADMIN')")
    op.alter_column("users", "role", server_default=None)


def downgrade():
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
