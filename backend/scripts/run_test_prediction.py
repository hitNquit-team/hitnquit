"""Create a persisted deterministic prediction for local verification."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import Employee, ExitFeedback, Resume, Tenant
from app.services.prediction_service import (
    AuthorizedTenantContext,
    WeightedRetentionPredictor,
    create_prediction,
)


EMBEDDING_DIMENSION = 384
NAMESPACE = uuid.UUID("0bc42c69-3fb6-4d4a-9a9f-df9b9bce3c6b")


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second] + [0.0] * (EMBEDDING_DIMENSION - 2)


def _stable_id(kind: str, record_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{record_id}")


async def _upsert_demo_data(session: AsyncSession, tenant: Tenant) -> uuid.UUID:
    records = (
        ("candidate", _vector(1.0), None, None),
        ("history-high", _vector(1.0), "resignation", 6),
        ("history-medium", _vector(0.8, 0.6), "relocation", 24),
        ("history-low", _vector(0.6, 0.8), "management", 36),
    )
    candidate_id = _stable_id("employee", "candidate")
    employee_ids = [
        _stable_id("employee", record_id)
        for record_id, _, _, _ in records
    ]
    resume_ids = [
        _stable_id("resume", record_id)
        for record_id, _, _, _ in records
    ]
    feedback_ids = [
        _stable_id("feedback", record_id)
        for record_id, _, exit_reason, _ in records
        if exit_reason is not None
    ]

    employees = await session.scalars(
        select(Employee).where(Employee.id.in_(employee_ids))
    )
    existing_employees = {employee.id: employee for employee in employees}

    resumes = await session.scalars(
        select(Resume).where(Resume.id.in_(resume_ids))
    )
    existing_resumes = {resume.id: resume for resume in resumes}

    feedbacks = await session.scalars(
        select(ExitFeedback).where(ExitFeedback.id.in_(feedback_ids))
    )
    existing_feedbacks = {
        feedback.id: feedback
        for feedback in feedbacks
    }

    for record_id, embedding, exit_reason, tenure_months in records:
        employee_id = _stable_id("employee", record_id)
        employee = existing_employees.get(employee_id)
        if employee is None:
            employee = Employee(
                id=employee_id,
                tenant_id=tenant.id,
                status="active" if exit_reason is None else "exited",
                source_dataset="test-prediction",
                source_record_id=record_id,
                tenure_months=tenure_months,
            )
            session.add(employee)
        else:
            employee.tenant_id = tenant.id
            employee.status = "active" if exit_reason is None else "exited"
            employee.tenure_months = tenure_months

        resume_id = _stable_id("resume", record_id)
        resume = existing_resumes.get(resume_id)
        if resume is None:
            session.add(
                Resume(
                    id=resume_id,
                    employee_id=employee_id,
                    embedding=embedding,
                    profile_text=f"Demo profile: {record_id}",
                )
            )
        else:
            resume.employee_id = employee_id
            resume.embedding = embedding

        if exit_reason is not None:
            feedback_id = _stable_id("feedback", record_id)
            feedback = existing_feedbacks.get(feedback_id)
            if feedback is None:
                session.add(
                    ExitFeedback(
                        id=feedback_id,
                        employee_id=employee_id,
                        exit_reason=exit_reason,
                        source_dataset="test-prediction",
                        source_record_id=record_id,
                    )
                )
            else:
                feedback.employee_id = employee_id
                feedback.exit_reason = exit_reason

    await session.flush()
    return candidate_id


async def _run(database_url: str, tenant_name: str) -> None:
    if "postgresql+asyncpg://" not in database_url:
        database_url = database_url.replace(
            "postgresql+psycopg2://",
            "postgresql+asyncpg://",
            1,
        )
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            tenant = await session.scalar(
                select(Tenant).where(Tenant.name == tenant_name)
            )
            if tenant is None:
                tenant = Tenant(name=tenant_name)
                session.add(tenant)
                await session.flush()

            candidate_id = await _upsert_demo_data(session, tenant)
            await session.commit()

            result = await create_prediction(
                session,
                auth_context=AuthorizedTenantContext(tenant_id=tenant.id),
                employee_id=candidate_id,
                resume_id=_stable_id("resume", "candidate"),
                predictor=WeightedRetentionPredictor(),
                neighbor_limit=3,
            )
            await session.commit()
            print(
                f"prediction_id={result.prediction.id} "
                f"tenant_id={tenant.id} status={result.status} "
                f"neighbors={len(result.neighbors)}"
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--tenant-name", default="local-test-prediction")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    asyncio.run(_run(args.database_url, args.tenant_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
