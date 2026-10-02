import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import ClientCtx, CurrentUser, DbSession
from app.schemas.common import Page
from app.schemas.event import (
    CheckInCode,
    CheckInRequest,
    CheckInResult,
    EventCreate,
    EventRead,
    EventUpdate,
)
from app.services import events as event_service

router = APIRouter(tags=["events"])


@router.get("/events")
def list_events(
    user: CurrentUser,
    db: DbSession,
    starts_after: datetime | None = None,
    starts_before: datetime | None = None,
    group_id: int | None = None,
    mine: Annotated[bool, Query(description="Only events of groups I belong to")] = False,
    building: Annotated[list[str], Query(description="Building code (repeatable)")] = [],  # noqa: B006
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[EventRead]:
    """Upcoming events (not yet ended unless ``starts_after`` says otherwise), soonest first."""
    items, total = event_service.list_events(
        db,
        user,
        starts_after=starts_after,
        starts_before=starts_before,
        group_id=group_id,
        only_my_groups=mine,
        buildings=building,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/events/{event_id}")
def get_event(
    event_id: int,
    user: CurrentUser,
    db: DbSession,
    context: ClientCtx,
    entry_point: str | None = Query(default=None, max_length=30),
    rec_request_id: uuid.UUID | None = None,
) -> EventRead:
    return event_service.get_event(
        db,
        user,
        event_id,
        entry_point=entry_point,
        rec_request_id=str(rec_request_id) if rec_request_id else None,
        context=context,
    )


@router.post("/groups/{group_id}/events", status_code=status.HTTP_201_CREATED)
def create_event(
    group_id: int, data: EventCreate, user: CurrentUser, db: DbSession, context: ClientCtx
) -> EventRead:
    """Publish an event (group admins). Members are notified."""
    event = event_service.create_event(db, user, group_id, data, context)
    return event_service.build_event_reads(db, user, [event.id])[0]


@router.patch("/events/{event_id}")
def update_event(
    event_id: int, changes: EventUpdate, user: CurrentUser, db: DbSession
) -> EventRead:
    event_service.update_event(db, user, event_id, changes)
    return event_service.build_event_reads(db, user, [event_id])[0]


@router.get("/events/{event_id}/check-in-code")
def get_check_in_code(event_id: int, user: CurrentUser, db: DbSession) -> CheckInCode:
    """QR code content for the venue (group admins)."""
    return event_service.get_check_in_code(db, user, event_id)


@router.post("/events/{event_id}/check-in")
def check_in(
    event_id: int, request: CheckInRequest, user: CurrentUser, db: DbSession, context: ClientCtx
) -> CheckInResult:
    """Check in by scanning the event QR code; send the phone location to verify proximity."""
    return event_service.check_in(db, user, event_id, request, context)
