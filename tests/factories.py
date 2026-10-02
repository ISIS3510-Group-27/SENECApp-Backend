"""Small helpers to create test data on top of the seeded reference catalog."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Interest, Membership, MembershipRole, StudentGroup, User


def make_user(db: Session, email: str, **fields: object) -> User:
    user = User(email=email, full_name=fields.pop("full_name", email.split("@")[0]), **fields)
    db.add(user)
    db.flush()
    return user


def group_by_name(db: Session, name: str) -> StudentGroup:
    return db.scalars(select(StudentGroup).where(StudentGroup.name == name)).one()


def interest_by_name(db: Session, name: str) -> Interest:
    return db.scalars(select(Interest).where(Interest.name == name)).one()


def add_member(
    db: Session, user: User, group: StudentGroup, role: MembershipRole = MembershipRole.MEMBER
) -> Membership:
    membership = Membership(user_id=user.id, group_id=group.id, role=role)
    db.add(membership)
    db.flush()
    return membership


def make_event(
    db: Session,
    group: StudentGroup,
    starts_in: timedelta = timedelta(days=2),
    duration: timedelta = timedelta(hours=2),
    **fields: object,
) -> Event:
    starts_at = datetime.now(UTC) + starts_in
    event = Event(
        group_id=group.id,
        title=fields.pop("title", f"{group.name} meetup"),
        starts_at=starts_at,
        ends_at=starts_at + duration,
        **fields,
    )
    db.add(event)
    db.flush()
    return event
