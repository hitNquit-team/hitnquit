from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
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


DEFAULT_MINIMUM_NEIGHBORS = 3
DEFAULT_NEIGHBOR_LIMIT = 10
EARLY_EXIT_HORIZON_MONTHS = 12
PREDICTOR_MODEL_VERSION = "weighted-neighbors-v1"
EVIDENCE_MATCH_COUNT = 5
EVIDENCE_SCHEMA_VERSION = "prediction-evidence-v1"


@dataclass(frozen=True, slots=True)
class AuthorizedTenantContext:
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PredictionValues:
    predicted_tenure: float | None
    risk_score: float | None
    likely_reason: str | None
    model_version: str
    confidence: float | None = None
    evidence: Mapping[str, Any] | None = None


class RetentionPredictor(Protocol):
    async def predict(
        self,
        *,
        candidate_embedding: Sequence[float],
        neighbors: Sequence[SimilarProfile],
    ) -> PredictionValues:
        ...


class WeightedRetentionPredictor:
    """Calculate MVP metrics from deterministic, similarity-weighted neighbors."""

    async def predict(
        self,
        *,
        candidate_embedding: Sequence[float],
        neighbors: Sequence[SimilarProfile],
    ) -> PredictionValues:
        # Neighbors are already filtered by `_is_metric_neighbor` in
        # `_run_prediction`, so they are used as-is without copying.
        if not neighbors:
            raise ValueError("at least one neighbor with a positive similarity and tenure is required")

        total_weight = sum(neighbor.similarity for neighbor in neighbors)
        if total_weight <= 0:
            raise ValueError("neighbor similarity weights must have a positive total")

        predicted_tenure = (
            sum(
                neighbor.similarity * neighbor.tenure_months
                for neighbor in neighbors
            )
            / total_weight
        )
        risk_score = (
            sum(
                neighbor.similarity
                for neighbor in neighbors
                if neighbor.tenure_months <= EARLY_EXIT_HORIZON_MONTHS
            )
            / total_weight
        )
        return PredictionValues(
            predicted_tenure=round(predicted_tenure, 2),
            risk_score=round(risk_score, 4),
            likely_reason=None,
            model_version=PREDICTOR_MODEL_VERSION,
        )


@dataclass(frozen=True, slots=True)
class PredictionRunResult:
    status: Literal["completed", "insufficient_data"]
    prediction: Prediction
    neighbors: tuple[SimilarProfile, ...]


class PredictionPersistenceError(RuntimeError):
    """Raised when a prediction and its matches cannot be persisted."""


class PredictionCalculationError(RuntimeError):
    """Raised when the predictor cannot calculate metrics for valid neighbors."""


async def predict_model2(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    candidate_embedding: Sequence[float],
    vacancy_id: uuid.UUID,
    employee_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Call the Model 2 adapter and return the public prediction response shape."""
    neighbors = await find_similar_profiles(
        session,
        tenant_id=tenant_id,
        candidate_embedding=candidate_embedding,
        limit=DEFAULT_NEIGHBOR_LIMIT,
        candidate_employee_id=employee_id,
    )
    status, values, evidence = await _run_prediction(
        predictor=WeightedRetentionPredictor(),
        candidate_embedding=candidate_embedding,
        neighbors=neighbors,
        required_neighbors=DEFAULT_MINIMUM_NEIGHBORS,
    )
    evidence["vacancy_id"] = str(vacancy_id)

    if status == "insufficient_data" or values is None:
        evidence["metric_availability"] = {
            "predicted_tenure": False,
            "risk_score": False,
            "likely_reason": False,
            "confidence": False,
        }
        return {
            "status": "insufficient_data",
            "predicted_tenure": None,
            "risk_score": None,
            "likely_reason": None,
            "evidence": evidence,
            "confidence": None,
        }

    evidence["model_version"] = values.model_version
    return {
        "status": "completed",
        "predicted_tenure": values.predicted_tenure,
        "risk_score": values.risk_score,
        "likely_reason": values.likely_reason,
        "evidence": evidence,
        "confidence": values.confidence,
    }


def _is_metric_neighbor(neighbor: SimilarProfile) -> bool:
    return (
        neighbor.similarity > 0
        and neighbor.tenure_months is not None
        and neighbor.tenure_months >= 0
    )


def _neighbor_evidence(neighbors: Sequence[SimilarProfile]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "employee_id": str(neighbor.employee_id),
            "similarity": round(neighbor.similarity, 4),
            "matched_exit_reason": neighbor.exit_reason,
        }
        for rank, neighbor in enumerate(neighbors, start=1)
    ]


def _reason_and_confidence(
    neighbors: Sequence[SimilarProfile],
) -> tuple[str | None, float | None]:
    """Derive explanation fields only from the ordered nearest matches."""
    evidence_neighbors = neighbors[:EVIDENCE_MATCH_COUNT]
    if not evidence_neighbors:
        return None, None

    reason_weights: dict[str, float] = {}
    first_reason_rank: dict[str, int] = {}
    for rank, neighbor in enumerate(evidence_neighbors):
        reason = neighbor.exit_reason.strip()
        reason_weights[reason] = reason_weights.get(reason, 0.0) + neighbor.similarity
        first_reason_rank.setdefault(reason, rank)

    likely_reason = min(
        reason_weights,
        key=lambda reason: (-reason_weights[reason], first_reason_rank[reason], reason),
    )
    total_weight = sum(neighbor.similarity for neighbor in evidence_neighbors)
    if total_weight <= 0:
        return likely_reason, 0.0

    mean_similarity = total_weight / len(evidence_neighbors)
    agreement = reason_weights[likely_reason] / total_weight
    confidence = round(max(0.0, min(1.0, mean_similarity * agreement)), 4)
    return likely_reason, confidence


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
    evidence_neighbors = neighbors[:EVIDENCE_MATCH_COUNT]
    likely_reason, confidence = _reason_and_confidence(evidence_neighbors)
    metric_neighbors = [neighbor for neighbor in neighbors if _is_metric_neighbor(neighbor)]

    if len(metric_neighbors) < required_neighbors:
        status: Literal["completed", "insufficient_data"] = "insufficient_data"
        values = None
        evidence: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "status": status,
            "neighbors": _neighbor_evidence(evidence_neighbors),
        }
    else:
        status = "completed"
        try:
            values = await predictor.predict(
                candidate_embedding=candidate_embedding,
                neighbors=metric_neighbors,
            )
        except ValueError as exc:
            raise PredictionCalculationError(
                "predictor failed to calculate metrics"
            ) from exc
        values = replace(
            values,
            likely_reason=likely_reason,
            confidence=confidence,
        )
        evidence = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "status": status,
            "neighbors": _neighbor_evidence(evidence_neighbors),
            "likely_reason": likely_reason,
            "confidence": confidence,
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
        confidence=None if values is None else _rounded_decimal(values.confidence, 4),
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
        await session.rollback()
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
    neighbor_limit: int = DEFAULT_NEIGHBOR_LIMIT,
    minimum_neighbors: int | None = DEFAULT_MINIMUM_NEIGHBORS,
    candidate_embedding: Sequence[float] | None = None,
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
    resolved_embedding = (
        resume.embedding
        if candidate_embedding is None
        else candidate_embedding
    )
    if not resolved_embedding:
        raise ValueError("candidate_embedding must not be empty")
    if not any(value != 0 for value in resolved_embedding):
        raise ValueError("candidate_embedding must not be a zero vector")
    if len(resolved_embedding) != len(resume.embedding):
        raise ValueError("candidate_embedding must match the resume embedding dimension")

    neighbors = await find_similar_profiles(
        session,
        tenant_id=auth_context.tenant_id,
        candidate_embedding=resolved_embedding,
        limit=neighbor_limit,
        candidate_employee_id=employee_id,
    )
    status, values, evidence = await _run_prediction(
        predictor=predictor,
        candidate_embedding=resolved_embedding,
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
