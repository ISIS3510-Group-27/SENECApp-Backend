"""Group admins (organizers) assigned by email.

An admin is an active membership with role ``admin``. When the email has no
account yet, a :class:`GroupAdminInvite` keeps the grant until that student
first signs in (see :func:`apply_invites`).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    GroupAdminInvite,
    Membership,
    MembershipRole,
    MembershipStatus,
    StudentGroup,
    User,
)
from app.services.errors import NotFoundError


@dataclass(frozen=True)
class GroupAdmins:
    admins: list[User]
    pending_emails: list[str]


def grant_admin(db: Session, group: StudentGroup, email: str) -> bool:
    """Make ``email`` an admin of ``group``. Returns True if applied now, False if
    it was saved as an invite for when that student first signs in.

    Does not commit: callers commit (one transaction per request or seed run).
    """
    email = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        db.execute(
            pg_insert(GroupAdminInvite)
            .values(group_id=group.id, email=email)
            .on_conflict_do_nothing(index_elements=["group_id", "email"])
        )
        return False

    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.group_id == group.id)
    )
    if membership is None:
        db.add(
            Membership(
                user_id=user.id,
                group_id=group.id,
                role=MembershipRole.ADMIN,
                status=MembershipStatus.ACTIVE,
            )
        )
    else:
        if membership.status != MembershipStatus.ACTIVE:
            membership.joined_at = datetime.now(UTC)
            membership.left_at = None
        membership.role = MembershipRole.ADMIN
        membership.status = MembershipStatus.ACTIVE
    db.execute(
        delete(GroupAdminInvite).where(
            GroupAdminInvite.group_id == group.id, GroupAdminInvite.email == email
        )
    )
    db.flush()
    return True


def revoke_admin(db: Session, group: StudentGroup, email: str) -> None:
    """Remove ``email`` as admin: drops a pending invite, or demotes the admin to
    a regular member (they stay in the group). Commits."""
    email = email.strip().lower()
    removed_invite = db.execute(
        delete(GroupAdminInvite).where(
            GroupAdminInvite.group_id == group.id, GroupAdminInvite.email == email
        )
    ).rowcount
    membership = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.group_id == group.id,
            func.lower(User.email) == email,
            Membership.role == MembershipRole.ADMIN,
        )
    )
    if membership is None and not removed_invite:
        raise NotFoundError(f"{email} is not an admin of this group")
    if membership is not None:
        membership.role = MembershipRole.MEMBER
    db.commit()


def list_admins(db: Session, group: StudentGroup) -> GroupAdmins:
    admins = db.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.group_id == group.id,
            Membership.role == MembershipRole.ADMIN,
            Membership.status == MembershipStatus.ACTIVE,
        )
        .order_by(User.email)
    ).all()
    pending = db.scalars(
        select(GroupAdminInvite.email)
        .where(GroupAdminInvite.group_id == group.id)
        .order_by(GroupAdminInvite.email)
    ).all()
    return GroupAdmins(admins=list(admins), pending_emails=list(pending))


def apply_invites(db: Session, user: User) -> int:
    """Turn the pending invites for ``user``'s email into admin memberships.
    Called when a student first signs in. Commits if anything changed."""
    groups = db.scalars(
        select(StudentGroup)
        .join(GroupAdminInvite, GroupAdminInvite.group_id == StudentGroup.id)
        .where(GroupAdminInvite.email == user.email.lower())
    ).all()
    for group in groups:
        grant_admin(db, group, user.email)
    if groups:
        db.commit()
    return len(groups)
