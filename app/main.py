import os
from fastapi import FastAPI

app = FastAPI(title="Vector Search API")

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "database_url": os.getenv("DATABASE_URL"),
        "log_level": os.getenv("LOG_LEVEL")
    }
