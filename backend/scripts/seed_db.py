"""Import the supplied HR files into PostgreSQL/pgvector.

The importer is deliberately source-oriented: every row gets a deterministic
source key, so rerunning the command does not create duplicate records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import Employee, ExitFeedback, Resume, Tenant

LOGGER = logging.getLogger("seed_db")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384
NAMESPACE = uuid.UUID("c1a8e7af-7dd2-4c8b-a5e8-92e66c236965")

ALIASES: dict[str, tuple[str, ...]] = {
    "record_id": ("EmployeeNumber", "employee_id", "Employee ID", "id", "ID"),
    "job_title": ("JobRole", "job_title", "Job Title", "position", "Position", "Business_Title"),
    "overtime": ("OverTime", "overtime", "Overtime", "haveOT"),
    "monthly_income": ("MonthlyIncome", "monthly_income", "Monthly Income"),
    "work_life_balance": ("WorkLifeBalance", "work_life_balance", "Work Life Balance", "WLB"),
    "years_at_company": (
        "YearsAtCompany", "years_at_company", "Tenure Years", "tenure_years", "Tenure_Years"
    ),
    "hire_date": ("hire_date", "HireDate", "Start Date", "start_date"),
    "exit_date": ("exit_date", "ExitDate", "Termination Date", "termination_date"),
    "attrition": ("Attrition", "attrition", "employment_status", "status", "Attrited"),
    "exit_reason": ("ExitReason", "exit_reason", "Reason for Leaving", "termination_reason"),
    "survey_date": ("SurveyDate", "survey_date", "Response Date", "response_date"),
}


@dataclass
class ImportSummary:
    imported: int = 0
    skipped: int = 0
    rejected: int = 0

    def report(self) -> dict[str, int]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "rejected": self.rejected,
        }


def _clean(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _column(row: pd.Series, name: str) -> Any:
    for alias in ALIASES[name]:
        if alias in row.index:
            value = _clean(row[alias])
            if value is not None:
                return value
    normalized = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in row.index}
    for alias in ALIASES[name]:
        column = normalized.get(re.sub(r"[^a-z0-9]", "", alias.lower()))
        if column is not None:
            value = _clean(row[column])
            if value is not None:
                return value
    return None


def _text(value: Any) -> str | None:
    value = _clean(value)
    return None if value is None else str(value)


def _number(value: Any) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    value = _clean(value)
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _boolean(value: Any) -> bool | None:
    value = _text(value)
    if value is None:
        return None
    if value.lower() in {"yes", "y", "true", "1"}:
        return True
    if value.lower() in {"no", "n", "false", "0"}:
        return False
    return None


def _source_id(row: pd.Series) -> str:
    explicit = _column(row, "record_id")
    if explicit is not None:
        return str(explicit)
    stable = "|".join("" if _clean(value) is None else str(_clean(value))
                      for value in row.tolist())
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".xlsx":
        sheets = pd.read_excel(path, sheet_name=None)
        if not sheets:
            return pd.DataFrame()
        return pd.concat(sheets.values(), ignore_index=True)
    return pd.read_csv(path)


def _profile(row: pd.Series) -> tuple[str, dict[str, Any]]:
    job = _text(_column(row, "job_title"))
    overtime = _boolean(_column(row, "overtime"))
    income = _number(_column(row, "monthly_income"))
    work_life = _number(_column(row, "work_life_balance"))
    years = _number(_column(row, "years_at_company"))
    months = None if years is None else round(years * 12)
    parts = [
        f"Job title: {job or 'unknown'}",
        f"Tenure: {months} months" if months is not None else "Tenure: unknown",
        f"Overtime: {'yes' if overtime else 'no'}" if overtime is not None else "Overtime: unknown",
        f"Monthly income: {income:g}" if income is not None else "Monthly income: unknown",
        f"Work-life balance: {int(work_life)}" if work_life is not None else "Work-life balance: unknown",
    ]
    normalized = {
        "job_title": job,
        "overtime": overtime,
        "monthly_income": income,
        "work_life_balance": None if work_life is None else int(work_life),
        "tenure_months": months,
        "missing_value_rule": "unknown values are retained as null and rendered as unknown",
        "units": {"monthly_income": "source currency/month", "tenure": "months"},
    }
    return "; ".join(parts), normalized


def _embedder() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required; install backend dependencies first"
        ) from exc
    model = SentenceTransformer(EMBEDDING_MODEL)
    if model.get_sentence_embedding_dimension() != EMBEDDING_DIMENSION:
        raise RuntimeError("Embedding dimension does not match resumes.embedding (384)")
    return model


def _stable_uuid(kind: str, tenant_id: uuid.UUID, dataset: str, record_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}|{tenant_id}|{dataset}|{record_id}")


def _load_file(session: Session, tenant: Tenant, path: Path, embedder: Any,
               summary: ImportSummary) -> None:
    dataset = path.name
    frame = _read(path)
    vectors = embedder.encode(
        [_profile(row)[0] for _, row in frame.iterrows()],
        normalize_embeddings=True,
        show_progress_bar=False,
    ) if len(frame) else []
    for index, (_, row) in enumerate(frame.iterrows()):
        record_id = _source_id(row)
        try:
            profile_text, normalized = _profile(row)
            employee_id = _stable_uuid("employee", tenant.id, dataset, record_id)
            employee = session.get(Employee, employee_id)
            if employee is not None and session.get(
                Resume, _stable_uuid("resume", tenant.id, dataset, record_id)
            ) is not None:
                summary.skipped += 1
                continue
            if employee is None:
                employee = Employee(
                    id=employee_id,
                    tenant_id=tenant.id,
                    source_dataset=dataset,
                    source_record_id=record_id,
                    status="exited" if _boolean(_column(row, "attrition")) else "active",
                )
                session.add(employee)
            employee.status = "exited" if _boolean(_column(row, "attrition")) else "active"
            employee.source_dataset = dataset
            employee.source_record_id = record_id
            employee.job_title = normalized["job_title"]
            employee.overtime = normalized["overtime"]
            employee.monthly_income = normalized["monthly_income"]
            employee.work_life_balance = normalized["work_life_balance"]
            employee.tenure_months = normalized["tenure_months"]
            employee.hire_date = _date(_column(row, "hire_date"))
            employee.exit_date = _date(_column(row, "exit_date"))

            resume_id = _stable_uuid("resume", tenant.id, dataset, record_id)
            resume = session.get(Resume, resume_id)
            if resume is None:
                resume = Resume(id=resume_id, employee_id=employee_id)
                session.add(resume)
            resume.employee_id = employee_id
            resume.profile_text = profile_text
            resume.parsed_skills = normalized
            resume.embedding = vectors[index].tolist()
            resume.source_uri = str(path.resolve())
            resume.parser_version = "hr-seed-v1"
            resume.embedding_model = EMBEDDING_MODEL

            reason = _text(_column(row, "exit_reason"))
            is_survey = path.suffix.lower() == ".xlsx"
            if is_survey or reason is not None or _column(row, "survey_date") is not None:
                feedback_id = _stable_uuid("feedback", tenant.id, dataset, record_id)
                feedback = session.get(ExitFeedback, feedback_id)
                if feedback is None:
                    feedback = ExitFeedback(id=feedback_id, employee_id=employee_id)
                    session.add(feedback)
                feedback.employee_id = employee_id
                feedback.exit_reason = reason
                feedback.source_dataset = dataset
                feedback.source_record_id = record_id
                feedback.details = {
                    "survey_date": _date(_column(row, "survey_date")).isoformat()
                    if _date(_column(row, "survey_date")) else None,
                    "reason_missing": reason is None,
                }
            summary.imported += 1
        except (TypeError, ValueError, KeyError) as exc:
            summary.rejected += 1
            LOGGER.warning("Rejected source row %s/%s: %s", dataset, index + 1, exc)
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    if args.skip_embeddings:
        raise SystemExit("--skip-embeddings is not supported: embeddings are required for Model 2")

    paths = [
        args.data_dir / "IBM HR Employee Attrition Data.csv",
        args.data_dir / "sample_hris_anonymized.csv",
        args.data_dir / "HR Employee Survey Responses_Dataset.xlsx",
    ]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        parser.error("Missing input files: " + ", ".join(missing))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    database_url = args.database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    engine = create_engine(database_url)
    summary = ImportSummary()
    with Session(engine) as session:
        tenant = session.scalar(select(Tenant).where(Tenant.name == args.tenant_name))
        if tenant is None:
            tenant = Tenant(name=args.tenant_name)
            session.add(tenant)
            session.flush()
        embedder = _embedder()
        for path in paths:
            _load_file(session, tenant, path, embedder, summary)
    print(json.dumps(summary.report(), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
