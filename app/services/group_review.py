"""Review of group proposals by platform admins (Uniandes Student Affairs).

Groups created through the API start as ``pending``. Approving makes them public
(search, recommendations, events) and announces them to interested students;
rejecting keeps them hidden. Either way the creator is notified.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    GroupAdminInvite,
    Membership,
    MembershipRole,
    MembershipStatus,
    NotificationType,
    ReviewStatus,
    StudentGroup,
    User,
)
from app.schemas.catalog import CategoryRead, TagRead
from app.schemas.group import GroupCreator, PendingGroupRead
from app.services import notifications
from app.services.analytics import track
from app.services.errors import ConflictError, NotFoundError


def list_pending(db: Session) -> list[PendingGroupRead]:
    """Proposals waiting for review, oldest first."""
    groups = db.scalars(
        select(StudentGroup)
        .where(StudentGroup.review_status == ReviewStatus.PENDING)
        .options(selectinload(StudentGroup.category), selectinload(StudentGroup.tags))
        .order_by(StudentGroup.created_at, StudentGroup.id)
    ).all()
    creators = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_({g.created_by_id for g in groups})))
    }
    return [
        PendingGroupRead(
            id=g.id,
            name=g.name,
            category=CategoryRead.model_validate(g.category),
            description=g.description,
            contact_email=g.contact_email,
            tags=[TagRead.model_validate(t) for t in sorted(g.tags, key=lambda t: t.name)],
            creator=(
                GroupCreator(id=creator.id, full_name=creator.full_name, email=creator.email)
                if (creator := creators.get(g.created_by_id))
                else None
            ),
            created_at=g.created_at,
        )
        for g in groups
    ]


def approve(db: Session, admin: User, group_id: int) -> StudentGroup:
    group = _pending_group(db, group_id)
    group.review_status = ReviewStatus.APPROVED
    group.rejection_reason = None
    group.reviewed_at = datetime.now(UTC)
    track(db, "group_approved", user_id=admin.id, properties=_review_properties(group))
    db.commit()

    if group.created_by_id is not None:
        notifications.notify(
            db,
            [group.created_by_id],
            NotificationType.GROUP_REVIEW,
            title="Your group was approved",
            body=f"{group.name} is now visible to all students.",
            group_id=group.id,
            data={"group_id": group.id, "review_status": ReviewStatus.APPROVED.value},
        )
    # Only approved groups are announced to students with matching interests.
    notifications.notify_new_group(db, group, creator_id=group.created_by_id)
    return group


def reject(db: Session, admin: User, group_id: int, reason: str) -> StudentGroup:
    group = _pending_group(db, group_id)
    group.review_status = ReviewStatus.REJECTED
    group.rejection_reason = reason
    group.reviewed_at = datetime.now(UTC)
    track(db, "group_rejected", user_id=admin.id, properties=_review_properties(group))
    db.commit()

    if group.created_by_id is not None:
        notifications.notify(
            db,
            [group.created_by_id],
            NotificationType.GROUP_REVIEW,
            title="Your group proposal was not approved",
            body=f"{group.name}: {reason}"[:500],
            group_id=group.id,
            data={"group_id": group.id, "review_status": ReviewStatus.REJECTED.value},
        )
    return group


def _pending_group(db: Session, group_id: int) -> StudentGroup:
    group = db.scalar(
        select(StudentGroup)
        .where(StudentGroup.id == group_id)
        .options(selectinload(StudentGroup.tags))
        .with_for_update()
    )
    if group is None:
        raise NotFoundError("Group not found")
    if group.review_status != ReviewStatus.PENDING:
        raise ConflictError(f"This group was already reviewed ({group.review_status.value})")
    return group


def _review_properties(group: StudentGroup) -> dict:
    return {
        "group_id": group.id,
        "creator_id": group.created_by_id,
        "category_id": group.category_id,
        "hours_pending": round((datetime.now(UTC) - group.created_at).total_seconds() / 3600, 2),
    }


# --- Group management (platform admins) -------------------------------------------------


def catalog_group_names() -> set[str]:
    """Names of the groups in the reference fixture (re-created on every start)."""
    from app.seed.loader import load_reference_fixtures

    return {g.name for g in load_reference_fixtures().student_groups}


def list_all_groups(db: Session) -> list[dict]:
    """Every group with its status, member count and admins, ordered by name."""
    counts = dict(
        db.execute(
            select(Membership.group_id, func.count(Membership.id))
            .where(Membership.status == MembershipStatus.ACTIVE)
            .group_by(Membership.group_id)
        ).all()
    )
    admins: dict[int, list[str]] = {}
    for group_id, email in db.execute(
        select(Membership.group_id, User.email)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.role == MembershipRole.ADMIN,
            Membership.status == MembershipStatus.ACTIVE,
        )
        .order_by(User.email)
    ):
        admins.setdefault(group_id, []).append(email)
    pending: dict[int, list[str]] = {}
    for group_id, email in db.execute(
        select(GroupAdminInvite.group_id, GroupAdminInvite.email).order_by(GroupAdminInvite.email)
    ):
        pending.setdefault(group_id, []).append(email)

    catalog = catalog_group_names()
    groups = db.scalars(
        select(StudentGroup)
        .options(selectinload(StudentGroup.category))
        .order_by(StudentGroup.name)
    )
    return [
        {
            "id": g.id,
            "name": g.name,
            "category": g.category.slug,
            "review_status": g.review_status.value,
            "is_active": g.is_active,
            "member_count": counts.get(g.id, 0),
            "admin_emails": admins.get(g.id, []),
            "pending_admin_emails": pending.get(g.id, []),
            "from_catalog": g.name in catalog,
        }
        for g in groups
    ]


def set_active(db: Session, admin: User, group_id: int, is_active: bool) -> StudentGroup:
    """Deactivate (hide from search, recommendations, joins and events) or reactivate."""
    group = _get(db, group_id)
    group.is_active = is_active
    track(
        db,
        "group_activated" if is_active else "group_deactivated",
        user_id=admin.id,
        properties={"group_id": group.id},
    )
    db.commit()
    return group


def delete_group(db: Session, admin: User, group_id: int) -> None:
    """Delete a group and everything attached to it (members, events, chat...).

    Catalog groups are refused: the next start would re-create them. Remove them
    from ``student_groups.json`` first, or deactivate them instead.
    """
    group = _get(db, group_id)
    if group.name in catalog_group_names():
        raise ConflictError(
            "This group is in app/seed/fixtures/student_groups.json and would be re-created "
            "on the next start. Remove it from the file and redeploy first, or deactivate it."
        )
    track(
        db,
        "group_deleted",
        user_id=admin.id,
        properties={"group_id": group.id, "name": group.name},
    )
    db.delete(group)
    db.commit()


def _get(db: Session, group_id: int) -> StudentGroup:
    group = db.get(StudentGroup, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group
