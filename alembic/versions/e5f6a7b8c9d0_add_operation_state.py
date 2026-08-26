"""Add resumable cursor state for non-destructive background scans."""
from alembic import op
import sqlalchemy as sa
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("operation_states", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(100), nullable=False, unique=True), sa.Column("cursor", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_operation_states_name", "operation_states", ["name"], unique=True)
def downgrade():
    op.drop_table("operation_states")
