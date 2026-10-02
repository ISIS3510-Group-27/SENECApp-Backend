import os
from collections.abc import Generator

# Tests must not depend on a developer's .env: environment variables take precedence
# over it, so pin the settings that change behavior before the app is imported.
os.environ.update({"AUTH_PROVIDER": "dev", "PUSH_PROVIDER": "none", "SCHEDULER_ENABLED": "false"})

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.verifiers import DevTokenVerifier, get_token_verifier  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed.reference import seed_reference  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://senecapp:senecapp@localhost:5433/senecapp_test"
)


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    """Fresh, migrated and reference-seeded test database (once per test run)."""
    url = make_url(TEST_DATABASE_URL)
    try:
        _create_database_if_missing(url)
    except OperationalError as exc:
        pytest.skip(f"Test database unavailable (is `docker compose up -d db` running?): {exc}")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    alembic_cfg.attributes["configure_logger"] = False
    command.upgrade(alembic_cfg, "head")

    with Session(engine) as session, session.begin():
        seed_reference(session)

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """Session whose changes (even committed ones) are rolled back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(_env_file=None, auth_provider="dev", allowed_email_domains="uniandes.edu.co")


@pytest.fixture
def client(db_session: Session, test_settings: Settings) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_token_verifier] = DevTokenVerifier
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_header(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{email}"}


def _create_database_if_missing(url: URL) -> None:
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin_engine.dispose()
