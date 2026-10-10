import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import ClientCtx, CurrentUser, DbSession
from app.models import EntryPoint
from app.schemas.common import Page
from app.schemas.group import (
    GroupCreate,
    GroupDetail,
    GroupFilters,
    GroupSort,
    GroupSummary,
    GroupUpdate,
    JoinRequest,
    MembershipRead,
)
from app.services import groups as group_service

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("")
def search_groups(
    user: CurrentUser,
    db: DbSession,
    context: ClientCtx,
    q: Annotated[
        str | None, Query(max_length=100, description="Text in name, description or tags")
    ] = None,
    category: Annotated[list[str], Query(description="Category slug (repeatable)")] = [],  # noqa: B006
    interest_id: Annotated[list[int], Query(description="Interest id (repeatable)")] = [],  # noqa: B006
    verified: bool | None = None,
    has_upcoming_events: bool | None = None,
    building: Annotated[list[str], Query(description="Meeting building code (repeatable)")] = [],  # noqa: B006
    sort: GroupSort = GroupSort.POPULAR,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[GroupSummary]:
    """Explore and search active groups. Searches with filters are logged for BQ12."""
    filters = GroupFilters(
        q=q,
        categories=category,
        interest_ids=interest_id,
        verified=verified,
        has_upcoming_events=has_upcoming_events,
        buildings=building,
        sort=sort,
    )
    items, total = group_service.search_groups(db, user, filters, limit, offset, context)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_group(
    data: GroupCreate, user: CurrentUser, db: DbSession, context: ClientCtx
) -> GroupDetail:
    """Propose a student group. It stays pending until approved; the creator joins it as
    a member (group admins are assigned by platform admins)."""
    group = group_service.create_group(db, user, data, context)
    return group_service.get_group_detail(db, user, group.id, context=context)


@router.get("/{group_id}")
def get_group(
    group_id: int,
    user: CurrentUser,
    db: DbSession,
    context: ClientCtx,
    entry_point: EntryPoint | None = None,
    rec_request_id: uuid.UUID | None = None,
) -> GroupDetail:
    """Group profile. Send ``entry_point`` (and ``rec_request_id`` when it came from a
    recommendation) so the view is attributed correctly."""
    return group_service.get_group_detail(
        db,
        user,
        group_id,
        entry_point=entry_point,
        rec_request_id=str(rec_request_id) if rec_request_id else None,
        context=context,
    )


@router.patch("/{group_id}")
def update_group(
    group_id: int, changes: GroupUpdate, user: CurrentUser, db: DbSession, context: ClientCtx
) -> GroupDetail:
    group_service.update_group(db, user, group_id, changes)
    return group_service.get_group_detail(db, user, group_id, context=context)


@router.put("/{group_id}/save", status_code=status.HTTP_204_NO_CONTENT)
def save_group(
    group_id: int,
    user: CurrentUser,
    db: DbSession,
    context: ClientCtx,
    source: Annotated[
        str | None, Query(max_length=30, description="Screen: explore, group_detail...")
    ] = None,
) -> Response:
    group_service.save_group(db, user, group_id, context, source)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{group_id}/save", status_code=status.HTTP_204_NO_CONTENT)
def unsave_group(group_id: int, user: CurrentUser, db: DbSession, context: ClientCtx) -> Response:
    group_service.unsave_group(db, user, group_id, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{group_id}/join")
def join_group(
    group_id: int, request: JoinRequest, user: CurrentUser, db: DbSession, context: ClientCtx
) -> MembershipRead:
    membership = group_service.join_group(db, user, group_id, request, context)
    return MembershipRead(
        group_id=membership.group_id,
        role=membership.role.value,
        status=membership.status.value,
        joined_at=membership.joined_at,
        entry_point=membership.entry_point.value if membership.entry_point else None,
    )


@router.delete("/{group_id}/membership", status_code=status.HTTP_204_NO_CONTENT)
def leave_group(group_id: int, user: CurrentUser, db: DbSession, context: ClientCtx) -> Response:
    group_service.leave_group(db, user, group_id, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
