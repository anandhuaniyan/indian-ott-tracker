"""Merge the Pinterest and movie-credit migration branches.

Revision ID: b9c0d1e2f3a4
Revises: a1b2c3d4f5e6, a8b9c0d1e2f3
Create Date: 2026-09-10
"""

from typing import Sequence, Union


revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = (
    "a1b2c3d4f5e6",
    "a8b9c0d1e2f3",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both already-independent schema branches."""


def downgrade() -> None:
    """Split the migration graph without altering either branch."""
