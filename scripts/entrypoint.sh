#!/bin/sh
set -e

echo "Applying database migrations..."
alembic upgrade head

echo "Seeding database (SEED_MODE=${SEED_MODE:-none})..."
python -m app.seed

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
