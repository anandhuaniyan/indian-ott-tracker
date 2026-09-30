"""Add private screenshot metadata to contact requests.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
"""

from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contact_requests", sa.Column("screenshot_path", sa.String(length=255)))
    op.add_column("contact_requests", sa.Column("screenshot_mime_type", sa.String(length=50)))
    op.add_column("contact_requests", sa.Column("screenshot_size", sa.Integer()))


def downgrade() -> None:
    op.drop_column("contact_requests", "screenshot_size")
    op.drop_column("contact_requests", "screenshot_mime_type")
    op.drop_column("contact_requests", "screenshot_path")
