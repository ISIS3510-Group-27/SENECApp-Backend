"""Members asking to become admins of a group, decided by that group's admins."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.client_context import ClientContext
from app.models import (
    AdminRequestStatus,
    GroupAdminRequest,
    Membership,
    MembershipRole,
    MembershipStatus,
    NotificationType,
    StudentGroup,
    User,
)
from app.services import group_admins, notifications
from app.services.analytics import track
from app.services.errors import ConflictError, NotFoundError
from app.services.groups import is_visible_to, require_group_admin, require_group_member


def request_admin(
    db: Session, user: User, group_id: int, note: str | None, context: ClientContext | None
) -> GroupAdminRequest:
    """A member asks to help run the group. One open request at a time."""
    group = _visible_group(db, user, group_id)
    membership = require_group_member(db, user, group_id)
    if membership.role == MembershipRole.ADMIN:
        raise ConflictError("You're already an admin of this group")
    if pending_request(db, user.id, group_id) is not None:
        raise ConflictError("You already asked; the group's admins haven't answered yet")

    request = GroupAdminRequest(group_id=group_id, user_id=user.id, note=note or None)
    db.add(request)
    db.flush()
    track(
        db,
        "admin_access_requested",
        user_id=user.id,
        context=context,
        properties={"group_id": group_id, "request_id": request.id},
    )
    db.commit()

    notifications.notify(
        db,
        group_admin_ids(db, group_id),
        NotificationType.ADMIN_REQUEST,
        title=f"Admin request for {group.name}",
        body=f"{user.full_name} wants to help run the group.",
        group_id=group_id,
        data={"group_id": group_id, "request_id": request.id, "status": "pending"},
    )
    return request


def list_pending(db: Session, user: User, group_id: int) -> list[GroupAdminRequest]:
    """Open requests, oldest first. Group admins only."""
    _visible_group(db, user, group_id)
    require_group_admin(db, user, group_id)
    return list(
        db.scalars(
            select(GroupAdminRequest)
            .where(
                GroupAdminRequest.group_id == group_id,
                GroupAdminRequest.status == AdminRequestStatus.PENDING,
            )
            .options(selectinload(GroupAdminRequest.user))
            .order_by(GroupAdminRequest.created_at, GroupAdminRequest.id)
        )
    )


def decide(
    db: Session,
    user: User,
    group_id: int,
    request_id: int,
    approve: bool,
    context: ClientContext | None,
) -> GroupAdminRequest:
    """Approve (the member becomes admin) or reject a request. Group admins only."""
    group = _visible_group(db, user, group_id)
    require_group_admin(db, user, group_id)
    request = db.scalar(
        select(GroupAdminRequest)
        .where(GroupAdminRequest.id == request_id, GroupAdminRequest.group_id == group_id)
        .options(selectinload(GroupAdminRequest.user))
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Request not found")
    if request.status != AdminRequestStatus.PENDING:
        raise ConflictError(f"This request was already {request.status.value}")

    requester: User = request.user
    if approve:
        still_member = db.scalar(
            select(Membership.id).where(
                Membership.group_id == group_id,
                Membership.user_id == requester.id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        if still_member is None:
            raise ConflictError(f"{requester.full_name} is no longer a member of this group")
        group_admins.grant_admin(db, group, requester.email)

    request.status = AdminRequestStatus.APPROVED if approve else AdminRequestStatus.REJECTED
    request.decided_at = datetime.now(UTC)
    request.decided_by_id = user.id
    track(
        db,
        "admin_access_approved" if approve else "admin_access_rejected",
        user_id=user.id,
        context=context,
        properties={"group_id": group_id, "request_id": request.id, "requester_id": requester.id},
    )
    db.commit()

    notifications.notify(
        db,
        [requester.id],
        NotificationType.ADMIN_REQUEST,
        title=(
            f"You're now an admin of {group.name}"
            if approve
            else f"Admin request for {group.name} not approved"
        ),
        body=(
            "You can create events and manage the group."
            if approve
            else "The group's admins declined your request. You can ask again later."
        ),
        group_id=group_id,
        data={"group_id": group_id, "request_id": request.id, "status": request.status.value},
    )
    return request


def pending_request(db: Session, user_id: int, group_id: int) -> GroupAdminRequest | None:
    return db.scalar(
        select(GroupAdminRequest).where(
            GroupAdminRequest.group_id == group_id,
            GroupAdminRequest.user_id == user_id,
            GroupAdminRequest.status == AdminRequestStatus.PENDING,
        )
    )


def pending_count(db: Session, group_id: int) -> int:
    return (
        db.scalar(
            select(func.count(GroupAdminRequest.id)).where(
                GroupAdminRequest.group_id == group_id,
                GroupAdminRequest.status == AdminRequestStatus.PENDING,
            )
        )
        or 0
    )


def group_admin_ids(db: Session, group_id: int) -> list[int]:
    return list(
        db.scalars(
            select(Membership.user_id).where(
                Membership.group_id == group_id,
                Membership.role == MembershipRole.ADMIN,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    )


def _visible_group(db: Session, user: User, group_id: int) -> StudentGroup:
    group = db.get(StudentGroup, group_id)
    if group is None or not is_visible_to(db, group, user):
        raise NotFoundError("Group not found")
    return group
