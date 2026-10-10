from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession, SettingsDep
from app.jobs.registry import JOBS, execute_job
from app.models import JobRun, RecommenderModel, ReengagementCase, Release, StudentGroup
from app.schemas.admin import (
    AddGroupAdmin,
    AdminGroupRead,
    GroupAdminsRead,
    GroupAdminUser,
    GroupStatusUpdate,
    JobInfo,
    JobRunRead,
    RecommenderModelRead,
    ReleaseIn,
    ReleaseRead,
)
from app.schemas.group import GroupReviewResult, PendingGroupRead, RejectRequest
from app.services import group_admins, group_review, reengagement
from app.services.errors import NotFoundError, ValidationFailedError

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


@router.get("/groups")
def list_groups(_admin: AdminUser, db: DbSession) -> list[AdminGroupRead]:
    """Every group (any status), with members and admins. Use it to find group ids."""
    return [AdminGroupRead(**g) for g in group_review.list_all_groups(db)]


@router.get("/groups/pending")
def list_pending_groups(_admin: AdminUser, db: DbSession) -> list[PendingGroupRead]:
    """Group proposals waiting for review, oldest first."""
    return group_review.list_pending(db)


@router.post("/groups/{group_id}/approve")
def approve_group(group_id: int, admin: AdminUser, db: DbSession) -> GroupReviewResult:
    """Publish a pending group. Its creator is notified, and so are students whose
    interests match its tags. 409 if the group is not pending."""
    return _review_result(group_review.approve(db, admin, group_id))


@router.post("/groups/{group_id}/reject")
def reject_group(
    group_id: int, body: RejectRequest, admin: AdminUser, db: DbSession
) -> GroupReviewResult:
    """Reject a pending group with a reason shown to its creator. 409 if not pending."""
    return _review_result(group_review.reject(db, admin, group_id, body.reason))


def _review_result(group: StudentGroup) -> GroupReviewResult:
    return GroupReviewResult(
        id=group.id,
        name=group.name,
        review_status=group.review_status.value,
        rejection_reason=group.rejection_reason,
        reviewed_at=group.reviewed_at,
    )


@router.patch("/groups/{group_id}")
def update_group_status(
    group_id: int, body: GroupStatusUpdate, admin: AdminUser, db: DbSession
) -> AdminGroupRead:
    """Deactivate a group (hidden from search, recommendations, joins and new events;
    data kept) or reactivate it."""
    group_review.set_active(db, admin, group_id, body.is_active)
    return _admin_group(db, group_id)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, admin: AdminUser, db: DbSession) -> Response:
    """Permanently delete a group with its members, events, chat and notifications.
    409 for catalog groups (remove them from student_groups.json first)."""
    group_review.delete_group(db, admin, group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/groups/{group_id}/admins")
def list_group_admins(group_id: int, _admin: AdminUser, db: DbSession) -> GroupAdminsRead:
    return _admins_read(db, _group(db, group_id))


@router.post("/groups/{group_id}/admins")
def add_group_admin(
    group_id: int, body: AddGroupAdmin, _admin: AdminUser, db: DbSession, settings: SettingsDep
) -> GroupAdminsRead:
    """Make a student an admin (organizer) of the group. If they have no account
    yet, they become admin on their first sign-in."""
    domain = body.email.rsplit("@", 1)[-1].lower()
    if domain not in settings.allowed_email_domains_list:
        raise ValidationFailedError("That email can't sign in to SENECApp (domain not allowed)")
    group = _group(db, group_id)
    group_admins.grant_admin(db, group, body.email)
    db.commit()
    return _admins_read(db, group)


@router.delete("/groups/{group_id}/admins/{email}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group_admin(group_id: int, email: str, _admin: AdminUser, db: DbSession) -> Response:
    """Remove an admin: they stay in the group as a regular member (or the pending
    invite is dropped)."""
    group_admins.revoke_admin(db, _group(db, group_id), email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _group(db: DbSession, group_id: int) -> StudentGroup:
    group = db.get(StudentGroup, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group


def _admins_read(db: DbSession, group: StudentGroup) -> GroupAdminsRead:
    result = group_admins.list_admins(db, group)
    return GroupAdminsRead(
        group_id=group.id,
        admins=[GroupAdminUser.model_validate(u) for u in result.admins],
        pending_emails=result.pending_emails,
    )


def _admin_group(db: DbSession, group_id: int) -> AdminGroupRead:
    return next(
        AdminGroupRead(**g) for g in group_review.list_all_groups(db) if g["id"] == group_id
    )
