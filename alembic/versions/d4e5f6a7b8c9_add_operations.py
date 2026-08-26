"""Add operational tracking tables without modifying existing movie data."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("movie_requests", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("request_id", sa.String(32), nullable=False, unique=True), sa.Column("movie_name", sa.String(500), nullable=False), sa.Column("email", sa.String(320), nullable=False), sa.Column("release_year", sa.Integer()), sa.Column("language", sa.String(20)), sa.Column("details", sa.Text()), sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    for c in ("request_id", "movie_name", "email", "status"): op.create_index(f"ix_movie_requests_{c}", "movie_requests", [c])
    op.create_table("ott_evidence", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="UNKNOWN"), sa.Column("platform", sa.String(100)), sa.Column("release_date", sa.Date()), sa.Column("source_url", sa.String(1000)), sa.Column("source_title", sa.String(500)), sa.Column("source_published_at", sa.Date()), sa.Column("discovered_at", sa.DateTime(timezone=True)), sa.Column("confidence", sa.Float(), nullable=False, server_default="0"), sa.Column("summary", sa.Text()), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_checked", sa.DateTime(timezone=True)), sa.Column("next_check", sa.DateTime(timezone=True)), sa.Column("notes", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    for c in ("movie_id", "status", "platform", "next_check"): op.create_index(f"ix_ott_evidence_{c}", "ott_evidence", [c])
    op.create_table("data_quality_issues", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE")), sa.Column("issue_type", sa.String(80), nullable=False), sa.Column("severity", sa.String(20), nullable=False, server_default="warning"), sa.Column("detail", sa.Text()), sa.Column("resolved_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_data_quality_issues_movie_id", "data_quality_issues", ["movie_id"]); op.create_index("ix_data_quality_issues_issue_type", "data_quality_issues", ["issue_type"])
    op.create_table("notification_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("fingerprint", sa.String(128), nullable=False), sa.Column("channel", sa.String(30), nullable=False), sa.Column("severity", sa.String(20), nullable=False), sa.Column("message", sa.Text(), nullable=False), sa.Column("last_notified_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_notification_logs_fingerprint", "notification_logs", ["fingerprint"])

def downgrade() -> None:
    op.drop_table("notification_logs"); op.drop_table("data_quality_issues"); op.drop_table("ott_evidence"); op.drop_table("movie_requests")
