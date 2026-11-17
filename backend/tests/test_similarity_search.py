from __future__ import annotations

import os
import uuid
from datetime import datetime
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Employee, ExitFeedback, Resume, Tenant
from app.services.similarity_search import find_similar_profiles


EMBEDDING_DIMENSION = 384
TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
CANDIDATE_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second] + [0.0] * (EMBEDDING_DIMENSION - 2)


class SimilaritySearchIntegrationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("TEST_DATABASE_URL is required for PostgreSQL integration tests")
        if database_url.startswith("postgresql+psycopg2://"):
            database_url = database_url.replace(
                "postgresql+psycopg2://",
                "postgresql+asyncpg://",
                1,
            )

        self.engine = create_async_engine(database_url)
        try:
            async with self.engine.connect():
                pass
        except (OSError, SQLAlchemyError) as exc:
            await self.engine.dispose()
            self.skipTest(f"test database is unavailable: {exc}")

        self.session = AsyncSession(self.engine)
        await self.session.begin()
        self.session.add_all(
            [
                Tenant(id=TENANT_ID, name="Tenant A"),
                Tenant(id=OTHER_TENANT_ID, name="Tenant B"),
            ]
        )
        await self.session.flush()
        await self._add_employee(
            CANDIDATE_ID,
            TENANT_ID,
            _vector(1.0),
            feedback_reason=None,
        )

    async def asyncTearDown(self) -> None:
        if hasattr(self, "session"):
            await self.session.rollback()
        if hasattr(self, "engine"):
            await self.engine.dispose()

    async def _add_employee(
        self,
        employee_id: uuid.UUID,
        tenant_id: uuid.UUID,
        embedding: list[float] | None,
        feedback_reason: str | None,
        submitted_at: datetime | None = None,
    ) -> None:
        self.session.add(
            Employee(
                id=employee_id,
                tenant_id=tenant_id,
                status="exited",
                source_dataset="test",
                source_record_id=str(employee_id),
            )
        )
        if embedding is not None:
            self.session.add(
                Resume(
                    id=uuid.uuid5(employee_id, "resume"),
                    employee_id=employee_id,
                    embedding=embedding,
                )
            )
        if feedback_reason is not None:
            self.session.add(
                ExitFeedback(
                    id=uuid.uuid5(employee_id, "feedback"),
                    employee_id=employee_id,
                    exit_reason=feedback_reason,
                    source_dataset="test",
                    source_record_id=str(employee_id),
                    submitted_at=submitted_at,
                )
            )
        await self.session.flush()

    async def test_returns_at_most_k_sorted_and_excludes_ineligible_profiles(self) -> None:
        eligible_high = uuid.UUID("00000000-0000-0000-0000-000000000011")
        eligible_low = uuid.UUID("00000000-0000-0000-0000-000000000012")
        other_tenant = uuid.UUID("00000000-0000-0000-0000-000000000013")
        no_embedding = uuid.UUID("00000000-0000-0000-0000-000000000014")
        no_feedback = uuid.UUID("00000000-0000-0000-0000-000000000015")
        blank_feedback = uuid.UUID("00000000-0000-0000-0000-000000000016")

        await self._add_employee(eligible_high, TENANT_ID, _vector(1.0), "resignation")
        await self._add_employee(eligible_low, TENANT_ID, _vector(0.8, 0.6), "relocation")
        await self._add_employee(other_tenant, OTHER_TENANT_ID, _vector(1.0), "other")
        await self._add_employee(no_embedding, TENANT_ID, None, "missing profile")
        await self._add_employee(no_feedback, TENANT_ID, _vector(1.0), None)
        await self._add_employee(blank_feedback, TENANT_ID, _vector(1.0), "   ")

        results = await find_similar_profiles(
            self.session,
            tenant_id=TENANT_ID,
            candidate_embedding=_vector(1.0),
            limit=2,
            candidate_employee_id=CANDIDATE_ID,
        )

        self.assertEqual([eligible_high, eligible_low], [result.employee_id for result in results])
        self.assertEqual(2, len(results))
        self.assertGreaterEqual(results[0].similarity, results[1].similarity)
        self.assertEqual(["resignation", "relocation"], [result.exit_reason for result in results])

    async def test_excludes_zero_and_negative_similarity(self) -> None:
        zero_similarity = uuid.UUID("00000000-0000-0000-0000-000000000021")
        negative_similarity = uuid.UUID("00000000-0000-0000-0000-000000000022")

        await self._add_employee(zero_similarity, TENANT_ID, _vector(0.0, 1.0), "orthogonal")
        await self._add_employee(negative_similarity, TENANT_ID, _vector(-1.0), "opposite")

        results = await find_similar_profiles(
            self.session,
            tenant_id=TENANT_ID,
            candidate_embedding=_vector(1.0),
            limit=10,
            candidate_employee_id=CANDIDATE_ID,
        )

        self.assertEqual([], results)

    async def test_equal_similarity_uses_employee_id_as_tiebreaker(self) -> None:
        first = uuid.UUID("00000000-0000-0000-0000-000000000031")
        second = uuid.UUID("00000000-0000-0000-0000-000000000032")

        await self._add_employee(second, TENANT_ID, _vector(1.0), "second")
        await self._add_employee(first, TENANT_ID, _vector(1.0), "first")

        results = await find_similar_profiles(
            self.session,
            tenant_id=TENANT_ID,
            candidate_embedding=_vector(1.0),
            limit=10,
            candidate_employee_id=CANDIDATE_ID,
        )

        self.assertEqual([first, second], [result.employee_id for result in results])

    async def test_returns_fewer_than_k_without_padding(self) -> None:
        only_match = uuid.UUID("00000000-0000-0000-0000-000000000041")
        await self._add_employee(only_match, TENANT_ID, _vector(1.0), "only match")

        results = await find_similar_profiles(
            self.session,
            tenant_id=TENANT_ID,
            candidate_embedding=_vector(1.0),
            limit=5,
            candidate_employee_id=CANDIDATE_ID,
        )

        self.assertEqual([only_match], [result.employee_id for result in results])

    async def test_returns_each_employee_once_when_multiple_resumes_match(self) -> None:
        employee_id = uuid.UUID("00000000-0000-0000-0000-000000000051")
        await self._add_employee(employee_id, TENANT_ID, _vector(1.0), "one employee")
        self.session.add(
            Resume(
                id=uuid.uuid4(),
                employee_id=employee_id,
                embedding=_vector(0.8, 0.6),
            )
        )
        await self.session.flush()

        results = await find_similar_profiles(
            self.session,
            tenant_id=TENANT_ID,
            candidate_embedding=_vector(1.0),
            limit=10,
            candidate_employee_id=CANDIDATE_ID,
        )

        self.assertEqual([employee_id], [result.employee_id for result in results])

    async def test_rejects_empty_embedding_and_nonpositive_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "candidate_embedding"):
            await find_similar_profiles(
                self.session,
                tenant_id=TENANT_ID,
                candidate_embedding=[],
            )

        with self.assertRaisesRegex(ValueError, "limit"):
            await find_similar_profiles(
                self.session,
                tenant_id=TENANT_ID,
                candidate_embedding=_vector(1.0),
                limit=0,
            )

