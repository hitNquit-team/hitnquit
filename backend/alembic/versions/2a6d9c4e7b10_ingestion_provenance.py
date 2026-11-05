"""Add normalized HR fields and ingestion provenance.

Revision ID: 2a6d9c4e7b10
Revises: f5d024b09d43
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "2a6d9c4e7b10"
down_revision: Union[str, Sequence[str], None] = "f5d024b09d43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("source_dataset", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("source_record_id", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("job_title", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("overtime", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("monthly_income", sa.Numeric(), nullable=True))
    op.add_column("employees", sa.Column("work_life_balance", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("tenure_months", sa.Integer(), nullable=True))
    op.add_column("resumes", sa.Column("profile_text", sa.Text(), nullable=True))
    op.add_column("resumes", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column("exit_feedbacks", sa.Column("source_dataset", sa.Text(), nullable=True))
    op.add_column("exit_feedbacks", sa.Column("source_record_id", sa.Text(), nullable=True))
    op.alter_column("exit_feedbacks", "exit_reason", nullable=True)

    # Existing rows cannot be assigned a truthful source identity. They remain
    # queryable, while new imports require provenance.
    op.create_index(
        "uq_employees_source",
        "employees",
        ["tenant_id", "source_dataset", "source_record_id"],
        unique=True,
        postgresql_where=sa.text("source_dataset IS NOT NULL AND source_record_id IS NOT NULL"),
    )
    op.create_index(
        "uq_feedback_source",
        "exit_feedbacks",
        ["employee_id", "source_dataset", "source_record_id"],
        unique=True,
        postgresql_where=sa.text("source_dataset IS NOT NULL AND source_record_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_feedback_source", table_name="exit_feedbacks")
    op.drop_index("uq_employees_source", table_name="employees")
    op.alter_column("exit_feedbacks", "exit_reason", nullable=False)
    for table, columns in (
        ("exit_feedbacks", ("source_record_id", "source_dataset")),
        ("resumes", ("embedding_model", "profile_text")),
        ("employees", ("tenure_months", "work_life_balance", "monthly_income",
                       "overtime", "job_title", "source_record_id", "source_dataset")),
    ):
        for column in columns:
            op.drop_column(table, column)
