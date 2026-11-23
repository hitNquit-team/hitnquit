from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Sequence
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from app.db.models import Prediction
from app.schemas.prediction import Model2PredictionRequest
from app.services.prediction_service import (
    PredictionCalculationError,
    PredictionValues,
    PredictionPersistenceError,
    WeightedRetentionPredictor,
    _create_prediction_record,
    _persist_prediction,
    _run_prediction,
    _validate_prediction_request,
    predict_model2,
)
from app.services.similarity_search import SimilarProfile


class DemoPredictor:
    def __init__(self) -> None:
        self.call_count = 0

    async def predict(
        self,
        *,
        candidate_embedding: Sequence[float],
        neighbors: Sequence[SimilarProfile],
    ) -> PredictionValues:
        self.call_count += 1
        return PredictionValues(
            predicted_tenure=24.0,
            risk_score=0.25,
            likely_reason=neighbors[0].exit_reason,
            model_version="test-v1",
            evidence={"neighbor_count": len(neighbors)},
        )


class PredictionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_weighted_prediction_uses_fixed_specification(self) -> None:
        neighbors = [
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=1.0,
                exit_reason="relocation",
                tenure_months=6,
            ),
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=0.8,
                exit_reason="management",
                tenure_months=24,
            ),
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=0.6,
                exit_reason="resignation",
                tenure_months=36,
            ),
        ]

        values = await WeightedRetentionPredictor().predict(
            candidate_embedding=[1.0, 0.0],
            neighbors=neighbors,
        )

        self.assertEqual(19.5, values.predicted_tenure)
        self.assertEqual(0.4167, values.risk_score)
        self.assertEqual("weighted-neighbors-v1", values.model_version)

    async def test_prediction_explanation_uses_top_five_and_is_deterministic(self) -> None:
        predictor = DemoPredictor()
        neighbors = [
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=similarity,
                exit_reason=reason,
                tenure_months=18,
            )
            for similarity, reason in (
                (1.0, "relocation"),
                (0.9, "management"),
                (0.8, "relocation"),
                (0.7, "resignation"),
                (0.6, "relocation"),
                (0.5, "management"),
            )
        ]

        first = await _run_prediction(
            predictor=predictor,
            candidate_embedding=[1.0, 0.0],
            neighbors=neighbors,
            required_neighbors=3,
        )
        second = await _run_prediction(
            predictor=DemoPredictor(),
            candidate_embedding=[1.0, 0.0],
            neighbors=neighbors,
            required_neighbors=3,
        )

        self.assertEqual("relocation", first[1].likely_reason)
        self.assertEqual(5, len(first[2]["neighbors"]))
        self.assertEqual(1, first[2]["neighbors"][0]["rank"])
        self.assertEqual(first[2]["confidence"], second[2]["confidence"])
        self.assertEqual(0.48, first[2]["confidence"])

    async def test_missing_tenure_reduces_metric_neighbor_count(self) -> None:
        predictor = DemoPredictor()
        neighbors = [
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=1.0,
                exit_reason="relocation",
                tenure_months=None,
            ),
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=0.8,
                exit_reason="management",
                tenure_months=24,
            ),
        ]

        status, values, evidence = await _run_prediction(
            predictor=predictor,
            candidate_embedding=[1.0, 0.0],
            neighbors=neighbors,
            required_neighbors=2,
        )

        self.assertEqual("insufficient_data", status)
        self.assertIsNone(values)
        self.assertEqual(0, predictor.call_count)

    def test_prediction_values_are_rounded_before_record_creation(self) -> None:
        prediction = _create_prediction_record(
            employee_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            values=PredictionValues(
                predicted_tenure=24.123,
                risk_score=0.20000000000000001,
                likely_reason="relocation",
                model_version="test-v1",
            ),
            evidence={"status": "completed"},
        )

        self.assertEqual(Decimal("24.12"), prediction.predicted_tenure)
        self.assertEqual(Decimal("0.2000"), prediction.risk_score)

    def test_validates_neighbor_limits(self) -> None:
        self.assertEqual(
            3,
            _validate_prediction_request(
                neighbor_limit=5,
                minimum_neighbors=3,
            ),
        )
        self.assertEqual(
            3,
            _validate_prediction_request(
                neighbor_limit=5,
                minimum_neighbors=None,
            ),
        )

        with self.assertRaisesRegex(ValueError, "neighbor_limit"):
            _validate_prediction_request(
                neighbor_limit=0,
                minimum_neighbors=None,
            )
        with self.assertRaisesRegex(ValueError, "at least"):
            _validate_prediction_request(
                neighbor_limit=2,
                minimum_neighbors=3,
            )

    async def test_insufficient_neighbors_skip_predictor(self) -> None:
        predictor = DemoPredictor()

        status, values, evidence = await _run_prediction(
            predictor=predictor,
            candidate_embedding=[1.0, 0.0],
            neighbors=[],
            required_neighbors=1,
        )

        self.assertEqual("insufficient_data", status)
        self.assertIsNone(values)
        self.assertEqual([], evidence["neighbors"])
        self.assertEqual(0, predictor.call_count)

    async def test_predictor_value_error_is_wrapped(self) -> None:
        predictor = Mock()
        predictor.predict = AsyncMock(side_effect=ValueError("bad neighbors"))
        neighbors = [
            SimilarProfile(
                employee_id=uuid.uuid4(),
                similarity=0.9,
                exit_reason="relocation",
                tenure_months=12,
            )
        ]

        with self.assertRaises(PredictionCalculationError) as raised:
            await _run_prediction(
                predictor=predictor,
                candidate_embedding=[1.0, 0.0],
                neighbors=neighbors,
                required_neighbors=1,
            )

        self.assertIsInstance(raised.exception.__cause__, ValueError)

    async def test_completed_prediction_includes_neighbor_and_predictor_evidence(
        self,
    ) -> None:
        predictor = DemoPredictor()
        neighbor = SimilarProfile(
            employee_id=uuid.uuid4(),
            similarity=0.75,
            exit_reason="relocation",
            tenure_months=18,
        )

        status, values, evidence = await _run_prediction(
            predictor=predictor,
            candidate_embedding=[1.0, 0.0],
            neighbors=[neighbor],
            required_neighbors=1,
        )

        self.assertEqual("completed", status)
        self.assertIsNotNone(values)
        self.assertEqual(1, predictor.call_count)
        self.assertEqual(
            "relocation",
            evidence["neighbors"][0]["matched_exit_reason"],
        )
        self.assertEqual(1, evidence["prediction"]["neighbor_count"])

    def test_model2_request_validates_required_fields_and_embedding_dimension(self) -> None:
        tenant_id = uuid.uuid4()
        vacancy_id = uuid.uuid4()
        valid_vector = [1.0] + [0.0] * 383

        request = Model2PredictionRequest(
            tenant_id=tenant_id,
            candidate_embedding=valid_vector,
            vacancy_id=vacancy_id,
        )
        self.assertEqual(tenant_id, request.tenant_id)
        self.assertEqual(vacancy_id, request.vacancy_id)
        self.assertEqual(384, len(request.candidate_embedding))

        with self.assertRaises(ValidationError):
            Model2PredictionRequest(
                tenant_id=tenant_id,
                candidate_embedding=[0.0] * 384,
                vacancy_id=vacancy_id,
            )
        with self.assertRaises(ValidationError):
            Model2PredictionRequest(
                tenant_id=tenant_id,
                candidate_embedding=[1.0] * 383,
                vacancy_id=vacancy_id,
            )
        with self.assertRaises(ValidationError):
            Model2PredictionRequest(
                tenant_id=tenant_id,
                candidate_embedding=valid_vector,
            )

    async def test_model2_adapter_returns_explicit_insufficient_metrics(self) -> None:
        session = Mock()
        tenant_id = uuid.uuid4()
        vacancy_id = uuid.uuid4()

        with patch(
            "app.services.prediction_service.find_similar_profiles",
            new=AsyncMock(return_value=[]),
        ):
            result = await predict_model2(
                session,
                tenant_id=tenant_id,
                candidate_embedding=[1.0] + [0.0] * 383,
                vacancy_id=vacancy_id,
            )

        self.assertEqual("insufficient_data", result["status"])
        self.assertIsNone(result["predicted_tenure"])
        self.assertIsNone(result["risk_score"])
        self.assertIsNone(result["likely_reason"])
        self.assertIsNone(result["confidence"])
        self.assertFalse(
            result["evidence"]["metric_availability"]["predicted_tenure"]
        )
        self.assertEqual(str(vacancy_id), result["evidence"]["vacancy_id"])

    async def test_flush_error_raises_persistence_error_without_committing(self) -> None:
        session = Mock()
        session.add = Mock()
        session.add_all = Mock()
        session.rollback = AsyncMock()
        session.flush = AsyncMock(
            side_effect=SQLAlchemyError("database unavailable")
        )
        session.commit = AsyncMock()
        prediction = Prediction(
            id=uuid.uuid4(),
            employee_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
        )

        with self.assertRaises(PredictionPersistenceError) as raised:
            await _persist_prediction(
                session,
                prediction=prediction,
                neighbors=[],
            )

        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()
        self.assertIsInstance(
            raised.exception.__cause__,
            SQLAlchemyError,
        )

