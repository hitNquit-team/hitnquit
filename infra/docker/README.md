B2B SaaS-платформа, яка застосовує методологію Win-Loss аналізу до найму та
утримання працівників.

## Налаштування

Створіть локальний env-файл. Він не повинен комітитися в репозиторій:

```powershell
cd D:\Hit&Quit\hitnquit\infra\docker
Copy-Item .env.example .env
```

У `infra/docker/.env` змініть щонайменше `POSTGRES_PASSWORD`. Для підключення
з контейнерів значення `DATABASE_URL` повинно містити host `db`, а не
`localhost`:

```text
postgresql+asyncpg://postgres:<password>@db:5432/app_db
```

## Запуск API

Запустіть базу та FastAPI:

```powershell
docker compose up --build -d
```

Контейнер `web` чекає, поки PostgreSQL стане healthy, автоматично виконує
`alembic upgrade head`, а потім запускає FastAPI без `--reload`. Seed не
запускається під час звичайного старту, оскільки має Compose profile `seed`.

Перевірте статус і API:

```powershell
docker compose ps
curl.exe http://localhost:8000/health
```

Поточний `/health` перевіряє лише доступність HTTP-застосунку; перевірка
реального з'єднання FastAPI з PostgreSQL буде додана окремо разом із database
engine/session.

## Запуск seed

Seed запускається окремо і лише за потреби:

```powershell
docker compose --profile seed run --rm seed
```

Він чекає на healthy PostgreSQL, читає datasets, генерує embeddings та
завершується після імпорту. Повторний запуск є ідемпотентним і не повинен
створювати дублікати.

Команда `docker compose run --rm seed` показує логи імпорту безпосередньо
в поточному терміналі та не перезапускає FastAPI.

`SEED_TENANT_NAME` використовується тільки `seed`. Для генерації embeddings
`seed` отримує пару `EMBEDDING_MODEL_PATH` і `EMBEDDING_MODEL_REVISION`; якщо
модель змінюється, revision потрібно змінити на сумісний commit тієї самої
моделі.

## Міграції та pgvector

Перед online-запуском Alembic автоматично виконує
`CREATE EXTENSION IF NOT EXISTS vector`, а потім застосовує міграції.
Initial migration містить таку саму ідемпотентну гарантію для offline
SQL-генерації. `init.sql` створює extension під час першої ініціалізації
нового PostgreSQL volume.

Перевірити таблиці, extension і версію міграції:

```powershell
docker compose exec db psql -U postgres -d app_db -c "\dt"
docker compose exec db psql -U postgres -d app_db -c "\dx"
docker compose exec db psql -U postgres -d app_db -c "SELECT version_num FROM alembic_version;"
```

Застосувати міграції вручну:

```powershell
docker compose exec web alembic -c backend/alembic.ini upgrade head
```

## Перевірка persistence

Перевірте кількість записів, перезапустіть API та перевірте її ще раз:

```powershell
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM tenants;"
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM employees;"
docker compose restart web
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM tenants;"
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM employees;"
```

`docker compose down` зберігає named volume `postgres_data`. Команда
`docker compose down -v` видаляє volume разом із даними і призначена лише для
повного очищення локального середовища.
