#!/bin/sh
set -e

alembic -c backend/alembic.ini upgrade head

python backend/scripts/seed_db.py \
  --data-dir backend/data/hr \
  --tenant-name "${SEED_TENANT_NAME:-Example organisation}"
  
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
