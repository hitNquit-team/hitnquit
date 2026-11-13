from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.session import engine, get_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown (коректне закриття пулу підключень)
    await engine.dispose()

app = FastAPI(title="Hit&Quit API", lifespan=lifespan)

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