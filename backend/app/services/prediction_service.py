from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
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


DEFAULT_MINIMUM_NEIGHBORS = 3


class PredictionPersistenceError(RuntimeError):
    """Raised when a prediction and its matches cannot be persisted."""


def _neighbor_evidence(neighbors: Sequence[SimilarProfile]) -> list[dict[str, Any]]:
    return [
        {
            "employee_id": str(neighbor.employee_id),
            "similarity": neighbor.similarity,
            "matched_exit_reason": neighbor.exit_reason,
        }
        for neighbor in neighbors
    ]


def _rounded_decimal(value: float | None, places: int) -> Decimal | None:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return Decimal(str(value)).quantize(quantum)


def _validate_prediction_request(
    *,
    neighbor_limit: int,
    minimum_neighbors: int | None,
) -> int:
    if neighbor_limit <= 0:
        raise ValueError("neighbor_limit must be greater than zero")

    required_neighbors = (
        DEFAULT_MINIMUM_NEIGHBORS
        if minimum_neighbors is None
        else minimum_neighbors
    )
    if required_neighbors <= 0:
        raise ValueError("minimum_neighbors must be greater than zero")
    if neighbor_limit < required_neighbors:
        raise ValueError("neighbor_limit must be at least minimum_neighbors")

    return required_neighbors


async def _load_prediction_resume(
    session: AsyncSession,
    *,
    auth_context: AuthorizedTenantContext,
    employee_id: uuid.UUID,
    resume_id: uuid.UUID,
) -> Resume:
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

    return resume


async def _run_prediction(
    *,
    predictor: RetentionPredictor,
    candidate_embedding: Sequence[float],
    neighbors: Sequence[SimilarProfile],
    required_neighbors: int,
) -> tuple[Literal["completed", "insufficient_data"], PredictionValues | None, dict[str, Any]]:
    neighbor_evidence = _neighbor_evidence(neighbors)

    if len(neighbors) < required_neighbors:
        status: Literal["completed", "insufficient_data"] = "insufficient_data"
        values = None
        evidence: dict[str, Any] = {
            "status": status,
            "neighbors": neighbor_evidence,
        }
    else:
        status = "completed"
        values = await predictor.predict(
            candidate_embedding=candidate_embedding,
            neighbors=neighbors,
        )
        evidence = {
            "status": status,
            "neighbors": neighbor_evidence,
        }
        if values.evidence:
            evidence["prediction"] = dict(values.evidence)

    return status, values, evidence


def _create_prediction_record(
    *,
    employee_id: uuid.UUID,
    resume_id: uuid.UUID,
    values: PredictionValues | None,
    evidence: dict[str, Any],
) -> Prediction:
    return Prediction(
        id=uuid.uuid4(),
        employee_id=employee_id,
        resume_id=resume_id,
        predicted_tenure=(
            None
            if values is None
            else _rounded_decimal(values.predicted_tenure, 2)
        ),
        risk_score=None if values is None else _rounded_decimal(values.risk_score, 4),
        likely_reason=None if values is None else values.likely_reason,
        evidence=evidence,
        model_version=None if values is None else values.model_version,
    )


async def _persist_prediction(
    session: AsyncSession,
    *,
    prediction: Prediction,
    neighbors: Sequence[SimilarProfile],
) -> None:
    session.add(prediction)

    try:
        await session.flush()
        session.add_all(
            PredictionMatch(
                id=uuid.uuid4(),
                prediction_id=prediction.id,
                matched_employee_id=neighbor.employee_id,
                similarity=_rounded_decimal(neighbor.similarity, 4),
                matched_exit_reason=neighbor.exit_reason,
            )
            for neighbor in neighbors
        )
        await session.flush()
    except SQLAlchemyError as exc:
        raise PredictionPersistenceError(
            "failed to persist prediction and prediction matches"
        ) from exc


async def create_prediction(
    session: AsyncSession,
    *,
    auth_context: AuthorizedTenantContext,
    employee_id: uuid.UUID,
    resume_id: uuid.UUID,
    predictor: RetentionPredictor,
    neighbor_limit: int = 10,
    minimum_neighbors: int | None = DEFAULT_MINIMUM_NEIGHBORS,
) -> PredictionRunResult:
    """Create one immutable prediction using only the caller's tenant history."""
    required_neighbors = _validate_prediction_request(
        neighbor_limit=neighbor_limit,
        minimum_neighbors=minimum_neighbors,
    )
    resume = await _load_prediction_resume(
        session,
        auth_context=auth_context,
        employee_id=employee_id,
        resume_id=resume_id,
    )

    neighbors = await find_similar_profiles(
        session,
        tenant_id=auth_context.tenant_id,
        candidate_embedding=resume.embedding,
        limit=neighbor_limit,
        candidate_employee_id=employee_id,
    )
    status, values, evidence = await _run_prediction(
        predictor=predictor,
        candidate_embedding=resume.embedding,
        neighbors=neighbors,
        required_neighbors=required_neighbors,
    )

    prediction = _create_prediction_record(
        employee_id=employee_id,
        resume_id=resume_id,
        values=values,
        evidence=evidence,
    )
    await _persist_prediction(
        session,
        prediction=prediction,
        neighbors=neighbors,
    )

    return PredictionRunResult(
        status=status,
        prediction=prediction,
        neighbors=tuple(neighbors),
    )
