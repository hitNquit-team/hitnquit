B2B SaaS-платформа, яка застосовує методологію Win-Loss аналізу до найму та
утримання працівників.

## Запуск через Docker

Перед першим запуском створіть локальний env-файл. Він не повинен
комітитися в репозиторій:

```powershell
cd D:\Hit&Quit\hitnquit\infra\docker
Copy-Item .env.example .env
```

У `infra/docker/.env` змініть щонайменше `POSTGRES_PASSWORD`. Для
підключення з контейнера `web` значення `DATABASE_URL` повинно містити host
`db`, а не `localhost`:

```text
postgresql+asyncpg://postgres:<password>@db:5432/app_db
```

Запустіть Compose з каталогу `infra/docker`:

```powershell
docker compose up --build
```

Контейнер `web` стартує тільки після того, як PostgreSQL стане healthy.
Після цього entrypoint автоматично виконує `alembic upgrade head`, імпортує
дані та запускає FastAPI. Якщо міграція або імпорт завершується з помилкою,
застосунок не запускається.

PostgreSQL extension `vector` створюється автоматично через `init.sql` під
час першої ініціалізації нового volume. Перед кожним online-запуском Alembic
також виконує `CREATE EXTENSION IF NOT EXISTS vector`, тому старий volume без
extension виправляється до застосування міграцій. Initial migration містить
ту саму ідемпотентну гарантію для offline SQL-генерації.

## Перевірка запуску

```powershell
docker compose ps
curl.exe http://localhost:8000/health
```

Поточний `/health` перевіряє лише доступність HTTP-застосунку; перевірка
реального з'єднання FastAPI з PostgreSQL буде додана окремо разом із database
engine/session.

Перевірити таблиці, extension і версію міграції можна так:

```powershell
docker compose exec db psql -U postgres -d app_db -c "\dt"
docker compose exec db psql -U postgres -d app_db -c "\dx"
docker compose exec db psql -U postgres -d app_db -c "SELECT version_num FROM alembic_version;"
```

## Перевірка persistence

Seed є ідемпотентним: повторний запуск не має створювати дублікати.
Перевірте кількість записів, перезапустіть сервіси та перевірте її ще раз:

```powershell
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM tenants;"
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM employees;"
docker compose restart
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM tenants;"
docker compose exec db psql -U postgres -d app_db -c "SELECT COUNT(*) FROM employees;"
```

`docker compose down` зберігає named volume `postgres_data`. Команда
`docker compose down -v` видаляє volume разом із даними і призначена лише для
повного очищення локального середовища.
