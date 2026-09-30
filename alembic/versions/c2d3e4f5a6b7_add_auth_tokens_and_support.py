"""Add secure account tokens and private Support contributions.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa


revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users",
        sa.Column("auth_session_version", sa.Integer(), server_default="0", nullable=False),
    )
    # Existing accounts predate verification. Preserve their access; only accounts
    # registered after this migration must verify before login.
    op.execute("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP WHERE email_verified_at IS NULL")

    op.create_table(
        "user_auth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "purpose IN ('VERIFY_EMAIL', 'PASSWORD_RESET')",
            name="ck_user_auth_token_purpose",
        ),
    )
    op.create_index("ix_user_auth_tokens_user_id", "user_auth_tokens", ["user_id"])
    op.create_index("ix_user_auth_tokens_purpose", "user_auth_tokens", ["purpose"])
    op.create_index("ix_user_auth_tokens_expires_at", "user_auth_tokens", ["expires_at"])

    op.create_table(
        "support_profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True)),
        sa.Column("enabled_by", sa.String(200)),
        *_timestamps(),
    )
    op.create_index("ix_support_profiles_enabled", "support_profiles", ["enabled"])

    op.create_table(
        "support_contributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="SGD", nullable=False),
        sa.Column("payment_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_method", sa.String(80), server_default="PayNow", nullable=False),
        sa.Column("status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("reference", sa.String(200)),
        sa.Column("admin_note", sa.Text()),
        *_timestamps(),
        sa.CheckConstraint("amount > 0", name="ck_support_contribution_amount_positive"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'FAILED', 'REFUNDED', 'CANCELLED')",
            name="ck_support_contribution_status",
        ),
    )
    op.create_index("ix_support_contributions_public_id", "support_contributions", ["public_id"])
    op.create_index("ix_support_contributions_user_id", "support_contributions", ["user_id"])
    op.create_index("ix_support_contributions_payment_date", "support_contributions", ["payment_date"])
    op.create_index("ix_support_contributions_status", "support_contributions", ["status"])

    op.create_table(
        "support_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("contribution_id", sa.Integer(), sa.ForeignKey("support_contributions.id", ondelete="SET NULL")),
        sa.Column("before_value", sa.JSON()),
        sa.Column("after_value", sa.JSON()),
        *_timestamps(),
        sa.CheckConstraint(
            "source IN ('WEB_ADMIN', 'DISCORD_ADMIN', 'SYSTEM')",
            name="ck_support_audit_source",
        ),
    )
    for name, columns in (
        ("ix_support_audit_logs_action", ["action"]),
        ("ix_support_audit_logs_source", ["source"]),
        ("ix_support_audit_logs_target_user_id", ["target_user_id"]),
        ("ix_support_audit_logs_contribution_id", ["contribution_id"]),
    ):
        op.create_index(name, "support_audit_logs", columns)

    op.create_table(
        "discord_interaction_receipts",
        sa.Column("interaction_id", sa.String(120), primary_key=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("discord_interaction_receipts")
    op.drop_table("support_audit_logs")
    op.drop_table("support_contributions")
    op.drop_table("support_profiles")
    op.drop_table("user_auth_tokens")
    op.drop_column("users", "auth_session_version")
    op.drop_column("users", "email_verified_at")
