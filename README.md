# SENECApp Backend

REST API and analytics pipeline for **SENECApp**, the student-groups app for Universidad de los Andes. It serves both mobile clients (Flutter and Kotlin) and collects the usage data that answers the project's 14 business questions.

## What's inside

| Area | Highlights |
|---|---|
| **Auth** | Firebase ID-token verification, restricted to verified `@uniandes.edu.co` accounts; dev tokens for local work |
| **Groups & Explore** | Search with filters, profiles, saves, join/leave, create/edit groups. New groups are proposals reviewed by admins (pending → approved/rejected) |
| **Events** | Listing, creation, **QR + GPS check-in** (sensor feature) |
| **Recommendations** | **Learning group recommender** (smart feature) and **"free right now" event suggestions** based on schedule, time and location (context-aware) |
| **Messaging** | Group chat, in-app notifications, push via Firebase Cloud Messaging |
| **Analytics** | Event ingestion, server-side tracking, 14 business-question queries, dashboard |
| **Experiments** | Detection of declining groups and a randomized re-engagement experiment (BQ10) |
| **Operations** | Background jobs (APScheduler), releases registry (BQ14), admin endpoints |

Docs for the app teams:

- [docs/frontend-integration.md](docs/frontend-integration.md): which endpoints implement each required feature, and how to connect from a phone
- [docs/event-taxonomy.md](docs/event-taxonomy.md): the analytics contract both apps must follow

## Tech stack

- Python 3.12+, [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- PostgreSQL 16 + SQLAlchemy 2.1 + Alembic
- Firebase Authentication (via `google-auth`) and Firebase Cloud Messaging (`firebase-admin`)
- APScheduler for background jobs
- Docker Compose for local hosting

## Getting started

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running) for both options, and Python 3.12+ for option B.

### 1. Configure `.env`

```bash
cp .env.example .env
```

`.env.example` is set up for the team's Firebase project (`senecapp`). Pick one setup:

| Setup | Values in `.env` | Flutter app |
|---|---|---|
| **Local, no Firebase** (quickest) | `AUTH_PROVIDER=dev`, `PUSH_PROVIDER=none` | `flutter run` |
| **Firebase** | Keep `AUTH_PROVIDER=firebase` and `FIREBASE_PROJECT_ID=senecapp`. For push, put the service-account key at `secrets/firebase-service-account.json` (see [Push notifications](#push-notifications)); without it, set `PUSH_PROVIDER=none` | `flutter run --dart-define=AUTH_MODE=firebase` |

Add your own email to `ADMIN_EMAILS` to use the admin endpoints and the dashboard.

> Keep comments on their own line in `.env`. A value like `AUTH_PROVIDER=dev#firebase` is not cut at the `#`, so the API won't start.

### 2. Option A: everything in Docker (recommended)

```bash
docker compose up --build
```

On startup the API container:

1. applies database migrations,
2. seeds the database (see [Seeding](#seeding)); the first cold start with `SEED_MODE=full` takes about a minute,
3. starts the API on port 8000 and the background scheduler.

| URL | What |
|---|---|
| http://localhost:8000/docs | Swagger UI (click **Authorize** and paste `dev:s.arango@uniandes.edu.co`) |
| http://localhost:8000/dashboard | Business-question dashboard (token `dev:admin@uniandes.edu.co`) |
| http://localhost:8000/api/v1/health/db | Health check |

The `dev:` tokens only work with `AUTH_PROVIDER=dev`. After changing `.env`, restart the API with `docker compose up -d` (no rebuild needed).

```bash
docker compose logs -f api # follow the API logs
docker compose down        # stop (data is kept in the pgdata volume)
docker compose down -v     # stop and wipe the database
```

### 2. Option B: API on your machine, database in Docker

```bash
docker compose up -d db          # PostgreSQL only, on host port 5433
python -m venv .venv
source .venv/bin/activate        # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

> The Docker database uses host port **5433**, so it doesn't clash with a locally installed PostgreSQL. In option B, `FIREBASE_CREDENTIALS_PATH` must be the key's local path (for example `secrets/firebase-service-account.json`), not `/code/...`.

### 3. Connect the app

Start the [Flutter app](https://github.com/ISIS3510-Group-27/SENECApp-Frontend-Flutter) with the matching sign-in mode from the table in step 1. The Android emulator reaches this API at `http://10.0.2.2:8000` without any extra setup. For a physical phone, see [Connecting to the local backend](docs/frontend-integration.md#connecting-to-the-local-backend).

## Seeding

`python -m app.seed` runs on every container start, controlled by `SEED_MODE`:

| `SEED_MODE` | What is loaded |
|---|---|
| `none` | Nothing |
| `reference` | Catalog from `app/seed/fixtures/`: categories, interests, campus buildings, 24 student groups and their tags |
| `full` (Docker default) | Catalog **plus ~12 weeks of simulated usage**: 300 students with schedules and interests, memberships, ~460 events, attendance, chat, notifications, recommendation logs, releases and ~65k analytics events |

- The catalog is **upserted by natural key**, so restarts never duplicate data and fixture edits are applied on the next start.
- The simulation runs **once** (recorded in `seed_runs`) and is deterministic (fixed random seed). Timestamps are relative to "now", so after a few weeks you may want fresh data: `python -m app.seed --reset`.
- Patterns are planted deliberately so every business question has a clear answer. They're documented at the top of [app/seed/simulation.py](app/seed/simulation.py).

```bash
python -m app.seed --mode full --reset                 # wipe and regenerate everything
docker compose exec api python -m app.seed --reset     # same, inside the running container
```

## Authentication

The apps sign in with Firebase and send `Authorization: Bearer <firebase-id-token>`. The backend checks the token's signature, expiry, audience and issuer against Google's public certificates, so it only needs the Firebase **project ID**. The first authenticated request creates the user, or links an existing user with the same email.

| `AUTH_PROVIDER` | Token accepted |
|---|---|
| `dev` (default when unset) | `dev:<email>`, for local development, Swagger and tests. Rejected at startup when `APP_ENV=production` |
| `firebase` | Real Firebase ID tokens. Set `FIREBASE_PROJECT_ID` |

Administrators (`ADMIN_EMAILS`) can use `/admin/*` and `/analytics/bq/*`, including the group review queue: `GET /admin/groups/pending`, `POST /admin/groups/{id}/approve`, `POST /admin/groups/{id}/reject`. See [docs/frontend-integration.md](docs/frontend-integration.md#creating-a-group-create-rso-review-flow).

## Push notifications

Notifications are always stored in the in-app inbox. To also push them to phones:

1. Download a service-account key (Firebase console → Project settings → Service accounts).
2. Save it as `secrets/firebase-service-account.json` (git-ignored).
3. Set `PUSH_PROVIDER=fcm` and `FIREBASE_CREDENTIALS_PATH=/code/secrets/firebase-service-account.json` (Docker) or the local path (Option B).

## Business questions

`GET /api/v1/analytics/bq` lists the questions, and `GET /api/v1/analytics/bq/{id}?days=90&app=flutter` answers one. Each answer returns the data plus a one-sentence takeaway. The dashboard at `/dashboard` shows all of them.

| BQ | Sources |
|---|---|
| 1, 11, 14 | Client `screen_view` / `app_error` events, `releases` |
| 2, 3 | `recommendation_logs`, memberships, `event_viewed`, attendance |
| 4, 6, 7, 12, 13 | Server events: `group_viewed` (with profile snapshot), `group_searched`, `group_saved`, `group_joined`; client `join_form_opened` |
| 5, 9 | `user_interests`, group tags, searches, joins, events |
| 8 | `notifications` (opened / dismissed) |
| 10 | Weekly attendance, `reengagement_cases` (randomized arms), attendance after the intervention |

## Background jobs

Enabled with `SCHEDULER_ENABLED=true` (the Docker default). Times are campus local time.

| Job | When | What |
|---|---|---|
| `detect_attendance_declines` | Daily 06:00 | Opens a re-engagement case for each group with a >30% decline over 4 weeks and randomly assigns *event reminders* or *group messages* |
| `send_event_reminders` | Hourly | Reminder arm: notifies lapsed members about events in the next 24 h |
| `send_reengagement_messages` | Daily 09:00 | Message arm: weekly message in the group chat |
| `evaluate_reengagement` | Daily 06:30 | Closes cases after 28 days and records the return-to-attendance rate |
| `train_recommender` | Daily 03:00 | Re-fits the group recommender on recent logs and activates it |

Run any job now with `POST /api/v1/admin/jobs/{name}/run` (admin).

## Development

```bash
docker compose up -d db   # tests use a separate "senecapp_test" database on the same server
pytest                    # run tests
ruff check .              # lint
ruff format .             # format
```

### Database migrations

```bash
alembic revision --autogenerate -m "describe change"   # create a migration from model changes
alembic upgrade head                                    # apply migrations
alembic downgrade -1                                    # roll back the last migration
```

New models must be imported in `app/models/__init__.py` so autogenerate can detect them. Autogenerate does **not** detect CHECK constraints added to existing tables; add those by hand.

## Project structure

```
app/
  analytics/        business-question queries (BQ1-BQ14)
  api/              HTTP routers and dependencies (DB session, current user, admin)
  auth/             ID-token verification (Firebase / dev)
  core/             configuration, client context headers, geo and text helpers
  db/               SQLAlchemy base, session and column types
  jobs/             background job registry and scheduler
  models/           ORM models
  recommendations/  recommender features, scoring and training (pure Python)
  schemas/          request/response models
  seed/             seed command, JSON fixtures and usage simulator
  services/         business logic used by the routers and jobs
  static/           dashboard page
  main.py           application factory / entry point
docs/               integration guide and event taxonomy for the app teams
migrations/         Alembic migration scripts
scripts/            container entrypoint
tests/              automated tests
```
