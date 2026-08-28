"""Add external movie identity to movie requests.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    # Existing historical requests cannot be assigned an external identity
    # safely. The public API requires it for every new request.
    op.add_column("movie_requests", sa.Column("external_movie_id", sa.Integer(), nullable=True))
    op.create_index("ix_movie_requests_external_movie_id", "movie_requests", ["external_movie_id"])
    op.create_index(
        "uq_movie_requests_active_external_id",
        "movie_requests",
        ["external_movie_id"],
        unique=True,
        postgresql_where=sa.text("external_movie_id IS NOT NULL AND status IN ('PENDING', 'REVIEWING')"),
    )


def downgrade():
    op.drop_index("uq_movie_requests_active_external_id", table_name="movie_requests")
    op.drop_index("ix_movie_requests_external_movie_id", table_name="movie_requests")
    op.drop_column("movie_requests", "external_movie_id")
