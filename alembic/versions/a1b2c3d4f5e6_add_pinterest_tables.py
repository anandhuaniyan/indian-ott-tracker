"""Add Pinterest publishing tables.

Revision ID: a1b2c3d4f5e6
Revises: f7c8d9e0f1a2
Create Date: 2026-09-10

Adds pinterest_boards, pinterest_pins, and pinterest_oauth_tokens tables
for the automated Pinterest publishing pipeline.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4f5e6"
down_revision: Union[str, Sequence[str], None] = "f7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pinterest boards
    op.create_table(
        "pinterest_boards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pinterest_board_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("language_code", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pinterest_boards_pinterest_board_id", "pinterest_boards", ["pinterest_board_id"], unique=True)
    op.create_index("ix_pinterest_boards_language_code", "pinterest_boards", ["language_code"])

    # Pinterest pins
    op.create_table(
        "pinterest_pins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("board_id", sa.Integer(), sa.ForeignKey("pinterest_boards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pin_type", sa.String(32), nullable=False),
        sa.Column("template", sa.String(32), nullable=False),
        sa.Column("pin_title", sa.String(200), nullable=False),
        sa.Column("pin_description", sa.Text(), nullable=False),
        sa.Column("destination_url", sa.String(2000), nullable=False),
        sa.Column("image_path", sa.String(1000), nullable=True),
        sa.Column("image_width", sa.Integer(), server_default=sa.text("1000"), nullable=False),
        sa.Column("image_height", sa.Integer(), server_default=sa.text("1500"), nullable=False),
        sa.Column("artwork_source", sa.String(50), nullable=True),
        sa.Column("artwork_rights_status", sa.String(32), server_default=sa.text("'RIGHTS_UNKNOWN'"), nullable=False),
        sa.Column("pinterest_pin_id", sa.String(100), nullable=True),
        sa.Column("pinterest_pin_url", sa.String(500), nullable=True),
        sa.Column("platform", sa.String(100), nullable=True),
        sa.Column("status", sa.String(32), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("movie_id", "pin_type", "platform", name="uq_pinterest_pin_idempotency"),
    )
    op.create_index("ix_pinterest_pins_movie_id", "pinterest_pins", ["movie_id"])
    op.create_index("ix_pinterest_pins_board_id", "pinterest_pins", ["board_id"])
    op.create_index("ix_pinterest_pins_pin_type", "pinterest_pins", ["pin_type"])
    op.create_index("ix_pinterest_pins_platform", "pinterest_pins", ["platform"])
    op.create_index("ix_pinterest_pins_artwork_rights_status", "pinterest_pins", ["artwork_rights_status"])
    op.create_index("ix_pinterest_pins_pinterest_pin_id", "pinterest_pins", ["pinterest_pin_id"])
    op.create_index("ix_pinterest_pins_status", "pinterest_pins", ["status"])

    # Pinterest OAuth tokens
    op.create_table(
        "pinterest_oauth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(50), server_default=sa.text("'bearer'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(500), nullable=True),
        sa.Column("pinterest_user_id", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pinterest_oauth_tokens")
    op.drop_table("pinterest_pins")
    op.drop_table("pinterest_boards")
