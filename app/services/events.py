"""Group events and attendance check-in."""

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.client_context import ClientContext
from app.core.config import get_settings
from app.core.geo import haversine_m
from app.models import (
    Attendance,
    CampusBuilding,
    CheckInMethod,
    Event,
    Membership,
    MembershipStatus,
    StudentGroup,
    User,
)
from app.schemas.catalog import BuildingRead
from app.schemas.event import (
    CheckInCode,
    CheckInRequest,
    CheckInResult,
    EventCreate,
    EventGroup,
    EventRead,
    EventUpdate,
)
from app.services import notifications
from app.services.analytics import track
from app.services.errors import ConflictError, NotFoundError, ValidationFailedError
from app.services.groups import require_group_admin


def list_events(
    db: Session,
    user: User,
    *,
    starts_after: datetime | None,
    starts_before: datetime | None,
    group_id: int | None,
    only_my_groups: bool,
    buildings: list[str],
    limit: int,
    offset: int,
) -> tuple[list[EventRead], int]:
    """Upcoming (by default) non-cancelled events, soonest first."""
    stmt = select(Event.id).where(~Event.is_cancelled)
    stmt = stmt.where(Event.ends_at >= (starts_after or datetime.now(UTC)))
    if starts_before:
        stmt = stmt.where(Event.starts_at < starts_before)
    if group_id is not None:
        stmt = stmt.where(Event.group_id == group_id)
    if only_my_groups:
        stmt = stmt.where(
            Event.group_id.in_(
                select(Membership.group_id).where(
                    Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE
                )
            )
        )
    if buildings:
        stmt = stmt.where(Event.building.has(CampusBuilding.code.in_(buildings)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    ids = list(db.scalars(stmt.order_by(Event.starts_at, Event.id).limit(limit).offset(offset)))
    return build_event_reads(db, user, ids), total


def build_event_reads(db: Session, user: User, event_ids: Sequence[int]) -> list[EventRead]:
    if not event_ids:
        return []
    events = {
        e.id: e
        for e in db.scalars(
            select(Event)
            .where(Event.id.in_(event_ids))
            .options(selectinload(Event.building), selectinload(Event.group))
        )
    }
    counts = dict(
        db.execute(
            select(Attendance.event_id, func.count(Attendance.id))
            .where(Attendance.event_id.in_(event_ids))
            .group_by(Attendance.event_id)
        ).all()
    )
    mine = set(
        db.scalars(
            select(Attendance.event_id).where(
                Attendance.user_id == user.id, Attendance.event_id.in_(event_ids)
            )
        )
    )
    return [
        _event_read(events[eid], counts.get(eid, 0), eid in mine)
        for eid in event_ids
        if eid in events
    ]


def _event_read(event: Event, attendee_count: int, checked_in: bool) -> EventRead:
    group: StudentGroup = event.group
    return EventRead(
        id=event.id,
        group_id=event.group_id,
        title=event.title,
        description=event.description,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        building=BuildingRead.model_validate(event.building) if event.building else None,
        location_detail=event.location_detail,
        capacity=event.capacity,
        is_cancelled=event.is_cancelled,
        group=EventGroup(id=group.id, name=group.name, color=group.color),
        attendee_count=attendee_count,
        checked_in=checked_in,
    )


def get_event(
    db: Session,
    user: User,
    event_id: int,
    entry_point: str | None = None,
    rec_request_id: str | None = None,
    context: ClientContext | None = None,
) -> EventRead:
    """Event detail; opening it is logged as ``event_viewed`` (BQ3 interactions)."""
    reads = build_event_reads(db, user, [event_id])
    if not reads:
        raise NotFoundError("Event not found")
    track(
        db,
        "event_viewed",
        user_id=user.id,
        context=context,
        screen="event_detail",
        properties={
            "event_id": event_id,
            "group_id": reads[0].group_id,
            "entry_point": entry_point or "direct",
            "rec_request_id": rec_request_id,
        },
    )
    db.commit()
    return reads[0]


def create_event(
    db: Session, user: User, group_id: int, data: EventCreate, context: ClientContext | None
) -> Event:
    group = db.get(StudentGroup, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    require_group_admin(db, user, group_id)
    if not group.is_active:
        raise ConflictError("Inactive groups cannot publish events")
    _check_building(db, data.building_id)

    event = Event(group_id=group_id, **data.model_dump())
    db.add(event)
    db.flush()
    track(
        db,
        "event_created",
        user_id=user.id,
        context=context,
        properties={"event_id": event.id, "group_id": group_id},
    )
    db.commit()
    notifications.notify_new_event(db, event.id, group, event.title, author_id=user.id)
    return event


def update_event(db: Session, user: User, event_id: int, changes: EventUpdate) -> Event:
    event = _get_event(db, event_id)
    require_group_admin(db, user, event.group_id)
    values = changes.model_dump(exclude_unset=True)
    if "building_id" in values:
        _check_building(db, values["building_id"])
    for field, value in values.items():
        setattr(event, field, value)
    db.commit()
    return event


def get_check_in_code(db: Session, user: User, event_id: int) -> CheckInCode:
    """The QR content organizers display at the venue. Group admins only."""
    event = _get_event(db, event_id)
    require_group_admin(db, user, event.group_id)
    return CheckInCode(
        event_id=event.id,
        code=event.check_in_code,
        qr_payload=f"senecapp://check-in?event_id={event.id}&code={event.check_in_code}",
    )


def check_in(
    db: Session,
    user: User,
    event_id: int,
    request: CheckInRequest,
    context: ClientContext | None,
    now: datetime | None = None,
) -> CheckInResult:
    """Register attendance by scanning the event's QR code (camera) and, optionally,
    confirming the phone is near the venue (GPS)."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    event = db.scalar(
        select(Event).where(Event.id == event_id).options(selectinload(Event.building))
    )
    if event is None:
        raise NotFoundError("Event not found")
    if not secrets.compare_digest(request.code, event.check_in_code):
        raise ValidationFailedError("Invalid check-in code")

    existing = db.scalar(
        select(Attendance).where(Attendance.event_id == event_id, Attendance.user_id == user.id)
    )
    if existing is not None:
        return CheckInResult(
            event_id=event_id,
            checked_in_at=existing.checked_in_at,
            distance_m=existing.distance_m,
            already_checked_in=True,
        )

    if event.is_cancelled:
        raise ConflictError("This event was cancelled")
    opens = event.starts_at - timedelta(minutes=settings.check_in_opens_minutes)
    closes = event.ends_at + timedelta(minutes=settings.check_in_closes_minutes)
    if not opens <= now <= closes:
        raise ConflictError("Check-in is only open around the event's time")

    distance_m = None
    if request.latitude is not None and request.longitude is not None and event.building:
        distance_m = round(
            haversine_m(
                request.latitude,
                request.longitude,
                event.building.latitude,
                event.building.longitude,
            ),
            1,
        )
        if distance_m > settings.check_in_max_distance_m:
            raise ConflictError("You seem to be too far from the event to check in")

    attendance = Attendance(
        event_id=event_id,
        user_id=user.id,
        method=CheckInMethod.QR,
        distance_m=distance_m,
        checked_in_at=now,
    )
    db.add(attendance)
    track(
        db,
        "event_checked_in",
        user_id=user.id,
        context=context,
        screen="check_in_scanner",
        properties={
            "event_id": event_id,
            "group_id": event.group_id,
            "method": CheckInMethod.QR.value,
            "distance_m": distance_m,
        },
    )
    db.commit()
    return CheckInResult(
        event_id=event_id, checked_in_at=now, distance_m=distance_m, already_checked_in=False
    )


def _get_event(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise NotFoundError("Event not found")
    return event


def _check_building(db: Session, building_id: int | None) -> None:
    if building_id is not None and db.get(CampusBuilding, building_id) is None:
        raise ValidationFailedError("Unknown building_id")
