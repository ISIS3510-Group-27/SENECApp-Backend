"""Data migrations, checked on a scratch database (not the shared test database)."""

from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from tests.conftest import TEST_DATABASE_URL, _create_database_if_missing

BEFORE_REVIEW_STATUS = "31bfb5d6d3d9"


@pytest.fixture
def scratch_db() -> Generator[tuple[Engine, Config], None, None]:
    url = make_url(TEST_DATABASE_URL).set(database="senecapp_test_migrations")
    try:
        _create_database_if_missing(url)
    except OperationalError as exc:
        pytest.skip(f"Test database unavailable: {exc}")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    config.attributes["configure_logger"] = False
    yield engine, config
    engine.dispose()


def test_existing_groups_become_approved(scratch_db: tuple[Engine, Config]) -> None:
    engine, config = scratch_db
    command.upgrade(config, BEFORE_REVIEW_STATUS)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO categories (slug, label) VALUES ('arts', 'Arts')"))
        connection.execute(
            text(
                "INSERT INTO student_groups (name, category_id, description) "
                "VALUES ('Legacy Group', 1, 'Created before the review flow existed.')"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT review_status, rejection_reason, reviewed_at FROM student_groups")
        ).one()
    assert tuple(row) == ("approved", None, None)

    command.downgrade(config, BEFORE_REVIEW_STATUS)  # the downgrade path works too
