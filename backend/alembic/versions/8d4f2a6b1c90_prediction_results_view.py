"""Add a read-only view for prediction results.

Revision ID: 8d4f2a6b1c90
Revises: 7c3e1f4a8b92
"""
from typing import Sequence, Union

from alembic import op


revision: str = "8d4f2a6b1c90"
down_revision: Union[str, Sequence[str], None] = "7c3e1f4a8b92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
