from contextlib import asynccontextmanager
import uuid

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.session import engine, get_db
from app.schemas.prediction import (
    Model2PredictionRequest,
    Model2PredictionResponse,
)
from app.services.prediction_service import predict_model2

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
    """Invoke the deterministic Model 2 adapter for a prototype prediction."""
    try:
        result = await predict_model2(
            db,
            tenant_id=payload.tenant_id,
            candidate_embedding=payload.candidate_embedding,
            vacancy_id=payload.vacancy_id,
            employee_id=payload.employee_id,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(exc),
            },
        ) from exc
    return Model2PredictionResponse.model_validate(result)