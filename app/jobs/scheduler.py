"""In-process scheduler (APScheduler). Enough for a single local API instance."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.jobs.registry import JOBS, run_job

logger = logging.getLogger(__name__)


def build_scheduler() -> BackgroundScheduler:
    timezone = get_settings().campus_timezone
    scheduler = BackgroundScheduler(timezone=timezone)
    for job in JOBS.values():
        scheduler.add_job(
            run_job,
            CronTrigger(timezone=timezone, **job.schedule),
            args=[job.name],
            id=job.name,
            name=job.description,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )
    return scheduler


def start_scheduler() -> BackgroundScheduler:
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started with jobs: %s", ", ".join(JOBS))
    return scheduler
