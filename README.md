# SENECApp Backend

REST API and analytics pipeline for **SENECApp**, the student-groups app for Universidad de los Andes.
It serves both mobile clients (Flutter and Kotlin) and collects the usage data used to answer the project's business questions.

## Tech stack

- Python 3.12+
- [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- PostgreSQL 16 + SQLAlchemy 2.0 + Alembic (migrations)
- Docker Compose for local hosting

## Getting started

### Option A: everything in Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

This starts PostgreSQL (data persisted in the `pgdata` volume), applies migrations, seeds the database (see [Seeding](#seeding)) and runs the API on port 8000.

```bash
docker compose down        # stop (data is kept)
docker compose down -v     # stop and wipe the database volume
```

### Option B: API on your machine, database in Docker

```bash
docker compose up -d db          # start only PostgreSQL (host port 5433)

# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows (PowerShell): .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements-dev.txt

# 3. Configure environment variables
cp .env.example .env

# 4. Apply migrations and run the API
alembic upgrade head
uvicorn app.main:app --reload
```

- Health check: http://localhost:8000/api/v1/health
- Database check: http://localhost:8000/api/v1/health/db
- Interactive docs (Swagger UI): http://localhost:8000/docs

## Development

```bash
pytest          # run tests
ruff check .    # lint
ruff format .   # format
```

### Seeding

On every startup the container runs `python -m app.seed`, controlled by `SEED_MODE`:

| `SEED_MODE` | What is loaded |
|---|---|
| `none` | Nothing |
| `reference` (default in Docker) | Catalog data from `app/seed/fixtures/`: categories, interests, campus buildings, student groups and their tags |

Seeding is **idempotent**: rows are upserted by natural key (slug, code or name), so restarts never duplicate data, and edits to the fixture files are applied to existing rows on the next start.

```bash
python -m app.seed                    # seed using SEED_MODE
python -m app.seed --mode reference   # override the mode
python -m app.seed --reset            # truncate all application tables, then seed
docker compose exec api python -m app.seed --reset   # same, inside the running container
```

> Campus building names and coordinates are approximate and only meant for demo purposes.

### Database migrations

```bash
alembic revision --autogenerate -m "describe change"   # create a migration from model changes
alembic upgrade head                                    # apply migrations
alembic downgrade -1                                    # roll back the last migration
```

New models must be imported in `app/models/__init__.py` so autogenerate can detect them.

## Project structure

```
app/
  api/          HTTP routers
  core/         configuration and cross-cutting concerns
  db/           SQLAlchemy base and session management
  models/       ORM models
  seed/         seed command and JSON fixtures
  main.py       application factory / entry point
migrations/     Alembic migration scripts
scripts/        container entrypoint and helper scripts
tests/          automated tests
```
