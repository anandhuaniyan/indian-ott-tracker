"""Add explicit OTT evidence provenance and verification state.

Revision ID: f4c5d6e7f8a9
Revises: f3b4c5d6e7f8
"""

from alembic import op
import sqlalchemy as sa


revision = "f4c5d6e7f8a9"
down_revision = "f3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ott_evidence", sa.Column("source_name", sa.String(length=200)))
    op.add_column(
        "ott_evidence",
        sa.Column("source_type", sa.String(length=50), server_default="unknown", nullable=False),
    )
    op.add_column(
        "ott_evidence",
        sa.Column("country", sa.String(length=10), server_default="IN", nullable=False),
    )
    op.add_column("ott_evidence", sa.Column("inspected_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ott_evidence",
        sa.Column("manually_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "ott_evidence",
        sa.Column("trusted", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("ott_evidence", sa.Column("rejected_at", sa.DateTime(timezone=True)))
    op.add_column("ott_evidence", sa.Column("rejection_reason", sa.Text()))
    op.create_index("ix_ott_evidence_source_type", "ott_evidence", ["source_type"])

    op.add_column(
        "ott_availability",
        sa.Column("verification_status", sa.String(length=20), server_default="UNKNOWN", nullable=False),
    )
    op.add_column("ott_availability", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ott_availability",
        sa.Column("manually_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "ott_availability",
        sa.Column(
            "evidence_id",
            sa.Integer(),
            sa.ForeignKey("ott_evidence.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_ott_availability_verification_status",
        "ott_availability",
        ["verification_status"],
    )
    op.create_index("ix_ott_availability_evidence_id", "ott_availability", ["evidence_id"])

    # Existing provider rows remain useful availability evidence, but dates are
    # not promoted without provenance. Existing research dates are deliberately
    # queued for review instead of being silently treated as confirmed.
    op.execute(
        """
        UPDATE ott_availability
        SET verification_status = CASE
            WHEN ott_release_date IS NULL THEN 'UNKNOWN'
            WHEN lower(source_type) = 'manual' THEN 'CONFIRMED'
            ELSE 'NEEDS_REVIEW'
        END,
        manually_verified = CASE WHEN lower(source_type) = 'manual' THEN TRUE ELSE FALSE END,
        verified_at = CASE WHEN lower(source_type) = 'manual' THEN last_checked ELSE NULL END
        """
    )


def downgrade() -> None:
    op.drop_index("ix_ott_availability_evidence_id", table_name="ott_availability")
    op.drop_index("ix_ott_availability_verification_status", table_name="ott_availability")
    op.drop_column("ott_availability", "evidence_id")
    op.drop_column("ott_availability", "manually_verified")
    op.drop_column("ott_availability", "verified_at")
    op.drop_column("ott_availability", "verification_status")
    op.drop_index("ix_ott_evidence_source_type", table_name="ott_evidence")
    for column in (
        "rejection_reason",
        "rejected_at",
        "trusted",
        "manually_verified",
        "inspected_at",
        "country",
        "source_type",
        "source_name",
    ):
        op.drop_column("ott_evidence", column)
