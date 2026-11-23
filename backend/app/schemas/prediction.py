from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


EMBEDDING_DIMENSION = 384


class Model2PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: uuid.UUID = Field(
        ...,
        description="Tenant identifier used to scope historical profile search.",
    )
    candidate_embedding: list[float] = Field(
        ...,
        validation_alias=AliasChoices(
            "candidate_embedding",
            "embedding",
            "profile_vector",
            "input_vector",
            "profile",
            "candidate_profile_vector",
        ),
        description="Non-zero normalized profile vector with exactly 384 values.",
    )
    vacancy_id: uuid.UUID = Field(
        ...,
        validation_alias=AliasChoices(
            "vacancy_id",
            "job_id",
            "job_reference",
            "vacancy_reference",
        ),
        description="Reference to the vacancy for which tenure and risk are evaluated.",
    )
    candidate_profile: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "candidate_profile",
            "input_profile",
            "approved_profile",
        ),
        description="Optional normalized candidate profile metadata.",
    )
    employee_id: uuid.UUID | None = Field(
        default=None,
        description="Optional employee ID excluded from historical similarity search.",
    )

    @model_validator(mode="before")
    @classmethod
    def extract_embedding_from_profile(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data = dict(values)
        if not any(
            name in data
            for name in (
                "candidate_embedding",
                "embedding",
                "profile_vector",
                "input_vector",
                "profile",
                "candidate_profile_vector",
            )
        ):
            profile = (
                data.get("candidate_profile")
                or data.get("input_profile")
                or data.get("approved_profile")
                or data.get("profile")
            )
            if isinstance(profile, dict):
                for name in (
                    "candidate_embedding",
                    "embedding",
                    "profile_vector",
                    "input_vector",
                ):
                    if name in profile:
                        data["candidate_embedding"] = profile[name]
                        break
        data.pop("input_profile", None)
        return data

    @model_validator(mode="after")
    def validate_embedding(self) -> "Model2PredictionRequest":
        if len(self.candidate_embedding) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"candidate_embedding must contain exactly {EMBEDDING_DIMENSION} values"
            )
        if any(
            isinstance(value, bool)
            or not math.isfinite(value)
            or value < -1.0
            or value > 1.0
            for value in self.candidate_embedding
        ):
            raise ValueError(
                "candidate_embedding values must be finite numbers between -1 and 1"
            )
        if not any(value != 0.0 for value in self.candidate_embedding):
            raise ValueError("candidate_embedding must not be a zero vector")
        return self


class Model2PredictionResponse(BaseModel):
    status: Literal["completed", "insufficient_data"] = Field(
        description="completed when enough valid history was available; otherwise insufficient_data.",
    )
    predicted_tenure: float | None = Field(
        default=None,
        description="Predicted tenure in months, or null when unavailable.",
    )
    risk_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Weighted early-exit risk in the range [0, 1], or null when unavailable.",
    )
    likely_reason: str | None = Field(
        default=None,
        description="Likely exit reason derived from nearest-neighbor feedback, or null.",
    )
    evidence: dict[str, Any] = Field(
        ...,
        description="Prediction evidence using the prediction-evidence-v1 schema.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Deterministic confidence score in [0, 1], or null when unavailable.",
    )


PredictionRequest = Model2PredictionRequest
PredictionResponse = Model2PredictionResponse
PredictionTestRequest = Model2PredictionRequest
PredictionTestResponse = Model2PredictionResponse
