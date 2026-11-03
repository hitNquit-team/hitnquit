import uuid
from datetime import datetime, date
from typing import List, Optional, Any

from sqlalchemy import String, Text, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

class Base(DeclarativeBase):
    """Базовий клас для всіх моделей SQLAlchemy"""
    pass

class Tenant(Base):
    __tablename__ = 'tenants'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Зв'язки
    employees: Mapped[List["Employee"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Employee(Base):
    __tablename__ = 'employees'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('tenants.id'), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    hire_date: Mapped[Optional[date]] = mapped_column(Date)
    exit_date: Mapped[Optional[date]] = mapped_column(Date)

    # Зв'язки
    tenant: Mapped["Tenant"] = relationship(back_populates="employees")
    resumes: Mapped[List["Resume"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    feedbacks: Mapped[List["ExitFeedback"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    predictions: Mapped[List["Prediction"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    
    # Зв'язок для випадків, коли цей працівник виступає як історичний збіг (matched_employee)
    matched_in_predictions: Mapped[List["PredictionMatch"]] = relationship(back_populates="matched_employee")


class Resume(Base):
    __tablename__ = 'resumes'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('employees.id'), nullable=False)
    parsed_skills: Mapped[Optional[dict]] = mapped_column(JSONB)
    
    # Розмірність 384 відповідає моделі all-MiniLM-L6-v2 з ваших налаштувань
    embedding: Mapped[Optional[Any]] = mapped_column(Vector(384))
    
    source_uri: Mapped[Optional[str]] = mapped_column(Text)
    parser_version: Mapped[Optional[str]] = mapped_column(Text)

    # Зв'язки
    employee: Mapped["Employee"] = relationship(back_populates="resumes")
    predictions: Mapped[List["Prediction"]] = relationship(back_populates="resume")


class ExitFeedback(Base):
    __tablename__ = 'exit_feedbacks'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('employees.id'), nullable=False)
    exit_reason: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Зв'язки
    employee: Mapped["Employee"] = relationship(back_populates="feedbacks")


class Prediction(Base):
    __tablename__ = 'predictions'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('employees.id'), nullable=False)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('resumes.id'), nullable=False)
    predicted_tenure: Mapped[Optional[float]] = mapped_column(Numeric)
    risk_score: Mapped[Optional[float]] = mapped_column(Numeric)
    likely_reason: Mapped[Optional[str]] = mapped_column(Text)
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_version: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Зв'язки
    employee: Mapped["Employee"] = relationship(back_populates="predictions")
    resume: Mapped["Resume"] = relationship(back_populates="predictions")
    matches: Mapped[List["PredictionMatch"]] = relationship(back_populates="prediction", cascade="all, delete-orphan")


class PredictionMatch(Base):
    __tablename__ = 'prediction_matches'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prediction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('predictions.id'), nullable=False)
    matched_employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('employees.id'), nullable=False)
    similarity: Mapped[Optional[float]] = mapped_column(Numeric)
    matched_exit_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Зв'язки
    prediction: Mapped["Prediction"] = relationship(back_populates="matches")
    matched_employee: Mapped["Employee"] = relationship(back_populates="matched_in_predictions")