"""Add deterministic confidence to predictions.

Revision ID: a1b2c3d4e5f6
Revises: 9e8a4c2d1f73
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9e8a4c2d1f73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("confidence", sa.Numeric(6, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "confidence")
