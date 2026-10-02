from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.jobs.registry import JOBS, execute_job
from app.models import JobRun, RecommenderModel, ReengagementCase, Release
from app.schemas.admin import JobInfo, JobRunRead, RecommenderModelRead, ReleaseIn, ReleaseRead
from app.services import reengagement

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/releases")
def list_releases(_admin: AdminUser, db: DbSession) -> list[ReleaseRead]:
    releases = db.scalars(select(Release).order_by(Release.released_at.desc()))
    return [ReleaseRead.model_validate(r) for r in releases]


@router.post("/releases", status_code=status.HTTP_201_CREATED)
def create_release(release: ReleaseIn, _admin: AdminUser, db: DbSession) -> ReleaseRead:
    """Register an app release and the feature area it changed (used by BQ14)."""
    exists = db.scalar(
        select(Release.id).where(Release.app == release.app, Release.version == release.version)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Release already registered")
    created = Release(**release.model_dump())
    db.add(created)
    db.commit()
    return ReleaseRead.model_validate(created)


@router.get("/jobs")
def list_jobs(_admin: AdminUser, db: DbSession) -> list[JobInfo]:
    latest = select(JobRun.job, func.max(JobRun.id).label("id")).group_by(JobRun.job).subquery()
    last_runs = {
        run.job: run for run in db.scalars(select(JobRun).join(latest, latest.c.id == JobRun.id))
    }
    return [
        JobInfo(
            name=job.name,
            description=job.description,
            schedule=job.schedule,
            last_run=JobRunRead.model_validate(last_runs[job.name])
            if job.name in last_runs
            else None,
        )
        for job in JOBS.values()
    ]


@router.post("/jobs/{job_name}/run")
def trigger_job(job_name: str, _admin: AdminUser, db: DbSession) -> JobRunRead:
    """Run a background job immediately (useful for demos)."""
    if job_name not in JOBS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job. Available: {sorted(JOBS)}")
    return JobRunRead.model_validate(execute_job(db, job_name))


@router.get("/reengagement/cases")
def list_reengagement_cases(_admin: AdminUser, db: DbSession) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    cases = db.scalars(select(ReengagementCase).order_by(ReengagementCase.detected_at.desc()))
    return [reengagement.case_summary(db, case, now) for case in cases]


@router.get("/recommender/models")
def list_recommender_models(_admin: AdminUser, db: DbSession) -> list[RecommenderModelRead]:
    models = db.scalars(select(RecommenderModel).order_by(RecommenderModel.trained_at.desc()))
    return [RecommenderModelRead.model_validate(m) for m in models]
