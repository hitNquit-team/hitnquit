from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Sequence
from unittest.mock import AsyncMock, Mock

from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Prediction
from app.services.prediction_service import (
    PredictionValues,
    PredictionPersistenceError,
    WeightedRetentionPredictor,
    _create_prediction_record,
    _persist_prediction,
    _run_prediction,
    _validate_prediction_request,
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

    async def test_flush_error_raises_persistence_error_without_committing(self) -> None:
        session = Mock()
        session.add = Mock()
        session.add_all = Mock()
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
        self.assertIsInstance(
            raised.exception.__cause__,
            SQLAlchemyError,
        )

