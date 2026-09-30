"""Add reviewed editorial updates and movie corrections.

Revision ID: f5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""
from alembic import op
import sqlalchemy as sa

revision = "f5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("editorial_articles",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("content_type", sa.String(40), nullable=False, server_default="EDITORIAL"),
        sa.Column("author_name", sa.String(255), nullable=False, server_default="OTTTracker Editorial"),
        sa.Column("reviewed_by", sa.String(255)), sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("seo_title", sa.String(255)),
        sa.Column("seo_description", sa.String(500)), sa.Column("hero_image", sa.String(1000)),
        sa.Column("source_period_start", sa.DateTime(timezone=True)), sa.Column("source_period_end", sa.DateTime(timezone=True)),
        sa.Column("is_indexable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ads_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_editorial_articles_slug"))
    for col in ("slug", "status", "content_type", "reviewed_at", "published_at"):
        op.create_index(f"ix_editorial_articles_{col}", "editorial_articles", [col])
    op.create_table("movie_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False), sa.Column("message", sa.Text(), nullable=False),
        sa.Column("email", sa.String(320)), sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_movie_corrections_movie_id", "movie_corrections", ["movie_id"])
    op.create_index("ix_movie_corrections_category", "movie_corrections", ["category"])
    op.create_index("ix_movie_corrections_status", "movie_corrections", ["status"])


def downgrade() -> None:
    op.drop_table("movie_corrections")
    op.drop_table("editorial_articles")
