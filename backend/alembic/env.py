import os
from dotenv import load_dotenv
from alembic import context
from sqlalchemy import engine_from_config, pool

# Завантажуємо змінні середовища
load_dotenv()

config = context.config

# Отримуємо DATABASE_URL з .env (за замовчуванням для локального запуску)
database_url = os.getenv("DATABASE_URL", "******localhost:5432/app_db")
if "postgresql+asyncpg://" in database_url:
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

config.set_main_option("sqlalchemy.url", database_url)

# Підключаємо метадані вашого базового класу моделей
from app.db.models import Base
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
