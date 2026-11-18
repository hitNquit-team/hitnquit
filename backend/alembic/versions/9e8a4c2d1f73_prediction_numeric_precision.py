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
    op.execute("DROP VIEW prediction_results")
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
    op.execute(
        """
        CREATE VIEW prediction_results AS
        SELECT
            p.id AS prediction_id,
            p.employee_id AS candidate_employee_id,
            e.tenant_id,
            p.resume_id,
            p.predicted_tenure,
            p.risk_score,
            p.likely_reason,
            p.evidence,
            p.model_version,
            p.created_at,
            pm.matched_employee_id,
            pm.similarity,
            pm.matched_exit_reason
        FROM predictions AS p
        JOIN employees AS e
          ON e.id = p.employee_id
        LEFT JOIN prediction_matches AS pm
          ON pm.prediction_id = p.id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW prediction_results")
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
    op.execute(
        """
        CREATE VIEW prediction_results AS
        SELECT
            p.id AS prediction_id,
            p.employee_id AS candidate_employee_id,
            e.tenant_id,
            p.resume_id,
            p.predicted_tenure,
            p.risk_score,
            p.likely_reason,
            p.evidence,
            p.model_version,
            p.created_at,
            pm.matched_employee_id,
            pm.similarity,
            pm.matched_exit_reason
        FROM predictions AS p
        JOIN employees AS e
          ON e.id = p.employee_id
        LEFT JOIN prediction_matches AS pm
          ON pm.prediction_id = p.id
        """
    )
    op.alter_column(
        "predictions",
        "predicted_tenure",
        existing_type=sa.Numeric(10, 2),
        type_=sa.Numeric(),
        existing_nullable=True,
    )
