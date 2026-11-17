from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Employee, Prediction, PredictionMatch, Resume
from app.services.similarity_search import (
    SimilarProfile,
    find_similar_profiles,
)


@dataclass(frozen=True, slots=True)
class AuthorizedTenantContext:
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PredictionValues:
    predicted_tenure: float | None
    risk_score: float | None
    likely_reason: str | None
    model_version: str
    evidence: Mapping[str, Any] | None = None


class RetentionPredictor(Protocol):
    async def predict(
        self,
        *,
        candidate_embedding: Sequence[float],
        neighbors: Sequence[SimilarProfile],
    ) -> PredictionValues:
        ...


@dataclass(frozen=True, slots=True)
class PredictionRunResult:
    status: Literal["completed", "insufficient_data"]
    prediction: Prediction
    neighbors: tuple[SimilarProfile, ...]


def _neighbor_evidence(neighbors: Sequence[SimilarProfile]) -> list[dict[str, Any]]:
    return [
        {
            "employee_id": str(neighbor.employee_id),
            "similarity": neighbor.similarity,
            "matched_exit_reason": neighbor.exit_reason,
        }
        for neighbor in neighbors
    ]


async def create_prediction(
    session: AsyncSession,
    *,
    auth_context: AuthorizedTenantContext,
    employee_id: uuid.UUID,
    resume_id: uuid.UUID,
    predictor: RetentionPredictor,
    neighbor_limit: int = 10,
    minimum_neighbors: int | None = None,
) -> PredictionRunResult:
    """Create one immutable prediction using only the caller's tenant history."""
    if neighbor_limit <= 0:
        raise ValueError("neighbor_limit must be greater than zero")
    required_neighbors = (
        neighbor_limit if minimum_neighbors is None else minimum_neighbors
    )
    if required_neighbors <= 0:
        raise ValueError("minimum_neighbors must be greater than zero")
    if neighbor_limit < required_neighbors:
        raise ValueError("neighbor_limit must be at least minimum_neighbors")

    employee = await session.scalar(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == auth_context.tenant_id,
        )
    )
    if employee is None:
        raise LookupError("employee does not belong to the authorized tenant")

    resume = await session.scalar(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.employee_id == employee_id,
        )
    )
    if resume is None:
        raise LookupError("resume does not belong to the employee")
    if resume.embedding is None:
        raise ValueError("resume embedding is required for prediction")

    neighbors = await find_similar_profiles(
        session,
        tenant_id=auth_context.tenant_id,
        candidate_embedding=resume.embedding,
        limit=neighbor_limit,
        candidate_employee_id=employee_id,
    )
    neighbor_evidence = _neighbor_evidence(neighbors)

    if len(neighbors) < required_neighbors:
        status = "insufficient_data"
        values = None
        evidence: dict[str, Any] = {
            "status": status,
            "neighbors": neighbor_evidence,
        }
    else:
        status = "completed"
        values = await predictor.predict(
            candidate_embedding=resume.embedding,
            neighbors=neighbors,
        )
        evidence = {
            "status": status,
            "neighbors": neighbor_evidence,
        }
        if values.evidence:
            evidence["prediction"] = dict(values.evidence)

    prediction = Prediction(
        id=uuid.uuid4(),
        employee_id=employee_id,
        resume_id=resume_id,
        predicted_tenure=None if values is None else values.predicted_tenure,
        risk_score=None if values is None else values.risk_score,
        likely_reason=None if values is None else values.likely_reason,
        evidence=evidence,
        model_version=None if values is None else values.model_version,
    )
    session.add(prediction)
    await session.flush()

    session.add_all(
        PredictionMatch(
            id=uuid.uuid4(),
            prediction_id=prediction.id,
            matched_employee_id=neighbor.employee_id,
            similarity=neighbor.similarity,
            matched_exit_reason=neighbor.exit_reason,
        )
        for neighbor in neighbors
    )
    await session.commit()

    return PredictionRunResult(
        status=status,
        prediction=prediction,
        neighbors=tuple(neighbors),
    )
