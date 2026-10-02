# SENECApp Backend

REST API and analytics pipeline for **SENECApp**, the student-groups app for Universidad de los Andes.
It serves both mobile clients (Flutter and Kotlin) and collects the usage data used to answer the project's business questions.

## Tech stack

- Python 3.12+
- [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn

## Getting started

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows (PowerShell): .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements-dev.txt

# 3. Configure environment variables
cp .env.example .env

# 4. Run the API
uvicorn app.main:app --reload
```

- Health check: http://localhost:8000/api/v1/health
- Interactive docs (Swagger UI): http://localhost:8000/docs

## Development

```bash
pytest          # run tests
ruff check .    # lint
ruff format .   # format
```

## Project structure

```
app/
  api/          HTTP routers
  core/         configuration and cross-cutting concerns
  main.py       application factory / entry point
tests/          automated tests
```
