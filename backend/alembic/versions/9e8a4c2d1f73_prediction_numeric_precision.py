"""Set fixed precision for prediction numeric values.

Revision ID: 9e8a4c2d1f73
Revises: 8d4f2a6b1c90
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9e8a4c2d1f73"
down_revision: Union[str, Sequence[str], None] = "8d4f2a6b1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "predictions",
        "predicted_tenure",
        existing_type=sa.Numeric(),
        type_=sa.Numeric(10, 2),
        existing_nullable=True,
    )
    op.alter_column(
        "predictions",
        "risk_score",
        existing_type=sa.Numeric(),
        type_=sa.Numeric(6, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "prediction_matches",
        "similarity",
        existing_type=sa.Numeric(),
        type_=sa.Numeric(6, 4),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "prediction_matches",
        "similarity",
        existing_type=sa.Numeric(6, 4),
        type_=sa.Numeric(),
        existing_nullable=True,
    )
    op.alter_column(
        "predictions",
        "risk_score",
        existing_type=sa.Numeric(6, 4),
        type_=sa.Numeric(),
        existing_nullable=True,
    )
    op.alter_column(
        "predictions",
        "predicted_tenure",
        existing_type=sa.Numeric(10, 2),
        type_=sa.Numeric(),
        existing_nullable=True,
    )
