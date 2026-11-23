from contextlib import asynccontextmanager
import uuid

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import engine, get_db
from app.schemas.prediction import (
    Model2PredictionRequest,
    Model2PredictionResponse,
)
from app.services.prediction_service import (
    AuthorizedTenantContext,
    PredictionPersistenceError,
    WeightedRetentionPredictor,
    create_prediction,
)
from app.db.models import Employee, Prediction

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown (коректне закриття пулу підключень)
    await engine.dispose()

app = FastAPI(title="Hit&Quit API", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "The prediction request contains missing or invalid fields.",
                "request_id": str(uuid.uuid4()),
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )

@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        # Виконуємо мінімальний запит для перевірки реального зв'язку з БД
        await db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected"
        }
    except Exception as e:
        # При відсутності зв'язку з БД повертаємо статус 503 Service Unavailable
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "database": "disconnected",
                "error": str(e)
            }
        )


@app.post(
    "/api/v1/predictions/test-model2",
    response_model=Model2PredictionResponse,
    responses={
        422: {
            "description": (
                "VALIDATION_ERROR: missing fields, invalid UUID values, "
                "or incompatible embedding vectors."
            )
        }
    },
)
async def test_model2_prediction(
    payload: Model2PredictionRequest,
    db: AsyncSession = Depends(get_db),
) -> Model2PredictionResponse:
    """Invoke Model 2 and atomically persist an immutable prediction record."""
    if payload.employee_id is None or payload.resume_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "employee_id and resume_id are required for prediction persistence.",
            },
        )

    try:
        run = await create_prediction(
            db,
            auth_context=AuthorizedTenantContext(tenant_id=payload.tenant_id),
            employee_id=payload.employee_id,
            resume_id=payload.resume_id,
            predictor=WeightedRetentionPredictor(),
            candidate_embedding=payload.candidate_embedding,
        )
        await db.commit()
    except PredictionPersistenceError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PERSISTENCE_ERROR",
                "message": "Prediction and evidence could not be saved atomically.",
            },
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PERSISTENCE_ERROR",
                "message": "Prediction and evidence could not be saved atomically.",
            },
        ) from exc
    except (LookupError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(exc),
            },
        ) from exc

    evidence = dict(run.prediction.evidence or {})
    evidence["vacancy_id"] = str(payload.vacancy_id)
    evidence.setdefault("model_version", run.prediction.model_version)

    return Model2PredictionResponse.model_validate({
        "status": run.status,
        "predicted_tenure": (
            None if run.prediction.predicted_tenure is None
            else float(run.prediction.predicted_tenure)
        ),
        "risk_score": (
            None if run.prediction.risk_score is None
            else float(run.prediction.risk_score)
        ),
        "likely_reason": run.prediction.likely_reason,
        "evidence": evidence,
        "confidence": (
            None if run.prediction.confidence is None
            else float(run.prediction.confidence)
        ),
    })


@app.get(
    "/api/v1/predictions/{prediction_id}",
    response_model=Model2PredictionResponse,
)
async def get_prediction(
    prediction_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Model2PredictionResponse:
    """Read one immutable tenant-scoped prediction record."""
    prediction = await db.scalar(
        select(Prediction)
        .join(Prediction.employee)
        .where(
            Prediction.id == prediction_id,
            Employee.tenant_id == tenant_id,
        )
    )
    if prediction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": "Prediction not found for the authorized tenant.",
            },
        )

    evidence = dict(prediction.evidence or {})
    status_value = evidence.get("status", "completed")
    return Model2PredictionResponse.model_validate({
        "status": status_value,
        "predicted_tenure": (
            None if prediction.predicted_tenure is None
            else float(prediction.predicted_tenure)
        ),
        "risk_score": (
            None if prediction.risk_score is None
            else float(prediction.risk_score)
        ),
        "likely_reason": prediction.likely_reason,
        "evidence": evidence,
        "confidence": (
            None if prediction.confidence is None
            else float(prediction.confidence)
        ),
    })