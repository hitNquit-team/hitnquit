"""Add indexes for tenant-scoped similarity search.

Revision ID: 7c3e1f4a8b92
Revises: 2a6d9c4e7b10
"""
from typing import Sequence, Union

from alembic import op


revision: str = "7c3e1f4a8b92"
down_revision: Union[str, Sequence[str], None] = "2a6d9c4e7b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_employees_tenant_id",
        "employees",
        ["tenant_id"],
    )
    op.create_index(
        "ix_resumes_employee_id",
        "resumes",
        ["employee_id"],
    )
    op.create_index(
        "ix_exit_feedbacks_employee_id",
        "exit_feedbacks",
        ["employee_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_exit_feedbacks_employee_id", table_name="exit_feedbacks")
    op.drop_index("ix_resumes_employee_id", table_name="resumes")
    op.drop_index("ix_employees_tenant_id", table_name="employees")
