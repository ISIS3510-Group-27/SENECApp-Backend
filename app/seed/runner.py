import logging
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal
from app.seed.reference import seed_reference

logger = logging.getLogger(__name__)


class SeedMode(StrEnum):
    NONE = "none"
    REFERENCE = "reference"


def run_seed(mode: SeedMode, reset: bool = False) -> None:
    """Seed the database in a single transaction (all or nothing)."""
    with SessionLocal.begin() as session:
        if reset:
            _reset_database(session)
        if mode is SeedMode.NONE:
            logger.info("SEED_MODE=none: skipping seed")
            return
        seed_reference(session)


def _reset_database(session: Session) -> None:
    if get_settings().app_env == "production":
        raise RuntimeError("Refusing to reset the database when APP_ENV=production")
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    logger.warning("Database reset: all application tables truncated")
