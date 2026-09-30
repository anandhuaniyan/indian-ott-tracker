"""Add community users and movie ratings.

Revision ID: e3f4a5b6c7d8
Revises: e2f3a4b5c6d7
"""

from alembic import op
import sqlalchemy as sa


revision = "e3f4a5b6c7d8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=100)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "community_ratings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Numeric(precision=3, scale=1), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rating >= 1 AND rating <= 10", name="ck_community_rating_range"),
        sa.CheckConstraint("rating * 2 = CAST(rating * 2 AS INTEGER)", name="ck_community_rating_half_step"),
        sa.UniqueConstraint("user_id", "movie_id", name="uq_community_rating_user_movie"),
    )
    op.create_index("ix_community_ratings_user_id", "community_ratings", ["user_id"])
    op.create_index("ix_community_ratings_movie_id", "community_ratings", ["movie_id"])


def downgrade() -> None:
    op.drop_index("ix_community_ratings_movie_id", table_name="community_ratings")
    op.drop_index("ix_community_ratings_user_id", table_name="community_ratings")
    op.drop_table("community_ratings")
    op.drop_table("users")
