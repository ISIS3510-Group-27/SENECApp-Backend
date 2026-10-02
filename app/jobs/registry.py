"""Background jobs. Each runs in its own DB session and is recorded in ``job_runs``."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import JobRun
from app.services import recommendations, reengagement

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    name: str
    description: str
    run: Callable[[Session, datetime], dict[str, Any]]
    # APScheduler cron fields, in campus local time.
    schedule: dict[str, Any]


def _detect(db: Session, now: datetime) -> dict[str, Any]:
    cases = reengagement.detect_declines(db, now)
    return {"cases_opened": len(cases), "groups": [c.group_id for c in cases]}


def _reminders(db: Session, now: datetime) -> dict[str, Any]:
    return {"reminders_sent": reengagement.send_event_reminders(db, now)}


def _messages(db: Session, now: datetime) -> dict[str, Any]:
    return {"messages_posted": reengagement.send_group_messages(db, now)}


def _evaluate(db: Session, now: datetime) -> dict[str, Any]:
    return {"cases_closed": reengagement.evaluate_cases(db, now)}


def _train(db: Session, now: datetime) -> dict[str, Any]:
    return recommendations.train_group_model(db, now).model_dump(exclude_none=True)


JOBS: dict[str, Job] = {
    job.name: job
    for job in (
        Job(
            "detect_attendance_declines",
            "Open re-engagement cases for groups whose attendance dropped >30% (BQ10)",
            _detect,
            {"hour": 6, "minute": 0},
        ),
        Job(
            "send_event_reminders",
            "Reminder arm: notify lapsed members about events in the next 24h",
            _reminders,
            {"minute": 0},
        ),
        Job(
            "send_reengagement_messages",
            "Message arm: weekly platform message in the group chat",
            _messages,
            {"hour": 9, "minute": 0},
        ),
        Job(
            "evaluate_reengagement",
            "Close finished re-engagement cases and record return rates",
            _evaluate,
            {"hour": 6, "minute": 30},
        ),
        Job(
            "train_recommender",
            "Re-fit the group recommender on recent recommendation logs",
            _train,
            {"hour": 3, "minute": 0},
        ),
    )
}


def execute_job(db: Session, name: str, now: datetime | None = None) -> JobRun:
    """Run a job in ``db``, recording its outcome. Raises ``KeyError`` for unknown jobs."""
    job = JOBS[name]
    now = now or datetime.now(UTC)
    run = JobRun(job=name, started_at=datetime.now(UTC), status="running")
    db.add(run)
    db.commit()
    try:
        details = job.run(db, now)
    except Exception as exc:
        db.rollback()
        logger.exception("Job %s failed", name)
        run.status, run.details = "failed", {"error": str(exc)}
    else:
        run.status, run.details = "succeeded", details
    run.finished_at = datetime.now(UTC)
    db.add(run)
    db.commit()
    return run


def run_job(name: str) -> JobRun:
    """Entry point for the scheduler: runs the job in a fresh session."""
    with SessionLocal() as db:
        return execute_job(db, name)
