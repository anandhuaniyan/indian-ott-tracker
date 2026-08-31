"""Add evidence-first OTT intelligence history and provider controls.

Revision ID: a6b7c8d9e0f1
Revises: f5d6e7f8a9b0
"""

from alembic import op
import sqlalchemy as sa


revision = "a6b7c8d9e0f1"
down_revision = "f5d6e7f8a9b0"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    evidence_columns = (
        sa.Column("fact_type", sa.String(40), server_default="RELEASE_DATE", nullable=False),
        sa.Column("availability_type", sa.String(30)),
        sa.Column("raw_external_id", sa.String(200)),
        sa.Column("movie_match_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("platform_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("date_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("verification_method", sa.String(30)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("ott_evidence.id", ondelete="SET NULL")),
    )
    for column in evidence_columns:
        op.add_column("ott_evidence", column)
    for column in ("fact_type", "availability_type", "raw_external_id", "verification_method", "observed_at", "superseded_by_id"):
        op.create_index(f"ix_ott_evidence_{column}", "ott_evidence", [column])
    op.execute(
        """
        UPDATE ott_evidence
        SET fact_type = CASE
            WHEN platform IS NOT NULL AND release_date IS NULL THEN 'AVAILABILITY'
            ELSE 'RELEASE_DATE'
        END,
        availability_type = CASE WHEN platform IS NOT NULL THEN 'SUBSCRIPTION' ELSE NULL END,
        movie_match_confidence = CASE WHEN manually_verified THEN 100 ELSE 80 END,
        platform_confidence = CASE WHEN platform IS NOT NULL THEN COALESCE(confidence, 0) ELSE 0 END,
        date_confidence = CASE WHEN release_date IS NOT NULL THEN COALESCE(confidence, 0) ELSE 0 END,
        verification_method = CASE WHEN manually_verified THEN 'MANUAL' ELSE 'AUTOMATED' END,
        observed_at = COALESCE(discovered_at, last_checked, created_at)
        """
    )

    availability_columns = (
        sa.Column("platform_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("date_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("verification_method", sa.String(30)),
        sa.Column("locked_by_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("observed_available_from", sa.DateTime(timezone=True)),
        sa.Column("is_original_premiere", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("release_state", sa.String(40), server_default="UNKNOWN", nullable=False),
        sa.Column("health_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("supporting_evidence_ids", sa.JSON()),
    )
    for column in availability_columns:
        op.add_column("ott_availability", column)
    for column in ("verification_method", "last_seen_at", "is_original_premiere", "release_state", "health_score"):
        op.create_index(f"ix_ott_availability_{column}", "ott_availability", [column])
    op.execute(
        """
        UPDATE ott_availability
        SET platform_confidence = confidence,
            date_confidence = CASE WHEN verification_status = 'CONFIRMED' AND ott_release_date IS NOT NULL THEN confidence ELSE 0 END,
            verification_method = CASE WHEN manually_verified THEN 'MANUAL' ELSE 'AUTOMATED' END,
            locked_by_admin = manually_verified,
            first_seen_at = COALESCE(last_checked, created_at),
            last_seen_at = COALESCE(last_checked, updated_at),
            release_state = CASE
                WHEN verification_status = 'NEEDS_REVIEW' THEN 'NEEDS_REVIEW'
                WHEN verification_status = 'CONFIRMED' AND ott_release_date > CURRENT_DATE THEN 'UPCOMING_CONFIRMED'
                WHEN verification_status = 'CONFIRMED' AND ott_release_date <= CURRENT_DATE THEN 'RELEASED_CONFIRMED'
                WHEN provider IS NOT NULL THEN 'PLATFORM_ONLY'
                ELSE 'UNKNOWN'
            END
        """
    )
    op.execute(
        """
        UPDATE ott_availability AS availability
        SET is_original_premiere = TRUE
        FROM (
            SELECT DISTINCT ON (movie_id, country) id
            FROM ott_availability
            WHERE verification_status = 'CONFIRMED'
              AND ott_release_date IS NOT NULL
            ORDER BY movie_id, country, ott_release_date, id
        ) AS earliest
        WHERE availability.id = earliest.id
        """
    )

    op.create_table(
        "ott_availability_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(100)),
        sa.Column("country", sa.String(10), server_default="IN", nullable=False),
        sa.Column("availability_type", sa.String(30), server_default="UNKNOWN", nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("raw_external_id", sa.String(200)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("ott_evidence.id", ondelete="SET NULL")),
        sa.Column("details", sa.JSON()),
        *_timestamps(),
    )
    for column in ("movie_id", "provider", "country", "availability_type", "available", "source_type", "raw_external_id", "observed_at", "evidence_id"):
        op.create_index(f"ix_ott_availability_observations_{column}", "ott_availability_observations", [column])

    op.create_table(
        "ott_reconciliation_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country", sa.String(10), server_default="IN", nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("platform", sa.String(100)),
        sa.Column("release_date", sa.Date()),
        sa.Column("availability_type", sa.String(30)),
        sa.Column("platform_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("date_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("movie_match_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("health_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("supporting_evidence_ids", sa.JSON()),
        sa.Column("conflicting_evidence_ids", sa.JSON()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
    )
    for column in ("movie_id", "country", "state", "platform", "release_date", "health_score", "decided_at", "is_current"):
        op.create_index(f"ix_ott_reconciliation_decisions_{column}", "ott_reconciliation_decisions", [column])

    op.create_table(
        "ott_provider_budget_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("period_type", sa.String(10), nullable=False),
        sa.Column("period_key", sa.String(10), nullable=False),
        sa.Column("request_limit", sa.Integer(), server_default="0", nullable=False),
        sa.Column("used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("provider", "period_type", "period_key", name="uq_ott_provider_budget_period"),
    )
    op.create_index("ix_ott_provider_budget_periods_provider", "ott_provider_budget_periods", ["provider"])
    op.create_index("ix_ott_provider_budget_periods_period_key", "ott_provider_budget_periods", ["period_key"])

    op.create_table(
        "ott_provider_health",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(30), server_default="DISABLED", nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("circuit_open_until", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_latency_ms", sa.Integer()),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("match_count", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
    )
    for column in ("provider", "status", "circuit_open_until"):
        op.create_index(f"ix_ott_provider_health_{column}", "ott_provider_health", [column])

    op.create_table(
        "ott_provider_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("cache_key", sa.String(250), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("provider", "cache_key", name="uq_ott_provider_cache"),
    )
    op.create_index("ix_ott_provider_cache_provider", "ott_provider_cache", ["provider"])
    op.create_index("ix_ott_provider_cache_expires_at", "ott_provider_cache", ["expires_at"])

    op.create_table(
        "ott_gold_set_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("expected_platform", sa.String(100)),
        sa.Column("expected_release_date", sa.Date()),
        sa.Column("expected_availability_type", sa.String(30)),
        sa.Column("expected_state", sa.String(40), server_default="UNKNOWN", nullable=False),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("notes", sa.Text()),
        sa.Column("manually_verified_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("movie_id", name="uq_ott_gold_set_movie"),
    )
    for column in ("movie_id", "language", "category"):
        op.create_index(f"ix_ott_gold_set_cases_{column}", "ott_gold_set_cases", [column])


def downgrade() -> None:
    for table in ("ott_gold_set_cases", "ott_provider_cache", "ott_provider_health", "ott_provider_budget_periods", "ott_reconciliation_decisions", "ott_availability_observations"):
        op.drop_table(table)
    for column in ("supporting_evidence_ids", "health_score", "release_state", "is_original_premiere", "observed_available_from", "last_seen_at", "first_seen_at", "locked_by_admin", "verification_method", "date_confidence", "platform_confidence"):
        op.drop_column("ott_availability", column)
    for column in ("superseded_by_id", "observed_at", "verification_method", "date_confidence", "platform_confidence", "movie_match_confidence", "raw_external_id", "availability_type", "fact_type"):
        op.drop_column("ott_evidence", column)
