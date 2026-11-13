import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Зчитуємо DATABASE_URL із змінних середовища (у Docker Compose там вказано хост 'db')
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://postgres:postgres@db:5432/app_db"
).strip("'\"")

# Якщо драйвер вказано як psycopg2, змінюємо на асинхронний asyncpg
if "postgresql+psycopg2://" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://")

# Створюємо асинхронний двигун
engine = create_async_engine(
    DATABASE_URL, 
    echo=False, 
    pool_pre_ping=True
)

# Фабрика асинхронних сесій
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Dependency для надання сесії в ендпоінтах FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session