from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Subquery

from app.db.models import Employee, ExitFeedback, Resume


@dataclass(frozen=True, slots=True)
class SimilarProfile:
    employee_id: uuid.UUID
    similarity: float
    exit_reason: str


def _latest_valid_feedback() -> Subquery:
    feedback_rank = func.row_number().over(
        partition_by=ExitFeedback.employee_id,
        order_by=(ExitFeedback.submitted_at.desc(), ExitFeedback.id.desc()),
    ).label("feedback_rank")

    return (
        select(
            ExitFeedback.employee_id,
            ExitFeedback.exit_reason,
            feedback_rank,
        )
        .where(func.btrim(ExitFeedback.exit_reason) != "")
        .subquery()
    )


async def find_similar_profiles(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    candidate_embedding: Sequence[float],
    limit: int = 10,
    candidate_employee_id: uuid.UUID | None = None,
) -> list[SimilarProfile]:
    """Return the deterministic, tenant-scoped nearest historical profiles."""
    if not candidate_embedding:
        raise ValueError("candidate_embedding must not be empty")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    feedback = _latest_valid_feedback()
    similarity_expression = 1 - Resume.embedding.cosine_distance(candidate_embedding)
    similarity = similarity_expression.label("similarity")

    filters = [
        Employee.tenant_id == tenant_id,
        Resume.embedding.is_not(None),
        similarity_expression > 0,
    ]
    if candidate_employee_id is not None:
        filters.append(Employee.id != candidate_employee_id)

    profile_rank = func.row_number().over(
        partition_by=Employee.id,
        order_by=(similarity_expression.desc(), Resume.id.asc()),
    ).label("profile_rank")
    candidate_profiles = (
        select(
            Employee.id.label("employee_id"),
            similarity,
            feedback.c.exit_reason,
            profile_rank,
        )
        .join(Resume, Resume.employee_id == Employee.id)
        .join(
            feedback,
            and_(
                feedback.c.employee_id == Employee.id,
                feedback.c.feedback_rank == 1,
            ),
        )
        .where(*filters)
        .subquery()
    )
    statement = (
        select(
            candidate_profiles.c.employee_id,
            candidate_profiles.c.similarity,
            candidate_profiles.c.exit_reason,
        )
        .where(candidate_profiles.c.profile_rank == 1)
        .order_by(
            candidate_profiles.c.similarity.desc(),
            candidate_profiles.c.employee_id.asc(),
        )
        .limit(limit)
    )

    result = await session.execute(statement)
    return [
        SimilarProfile(
            employee_id=employee_id,
            similarity=float(profile_similarity),
            exit_reason=exit_reason,
        )
        for employee_id, profile_similarity, exit_reason in result.all()
    ]
