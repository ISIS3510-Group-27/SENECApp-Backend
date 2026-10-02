"""Student groups: Explore search, profiles, saves and memberships."""

import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, selectinload

from app.core.client_context import ClientContext
from app.models import (
    CampusBuilding,
    Category,
    EntryPoint,
    Event,
    GroupInterest,
    GroupSave,
    Interest,
    Membership,
    MembershipRole,
    MembershipStatus,
    ReviewStatus,
    StudentGroup,
    User,
    UserInterest,
)
from app.schemas.catalog import BuildingRead, CategoryRead, TagRead
from app.schemas.event import EventSummary
from app.schemas.group import (
    GroupCreate,
    GroupDetail,
    GroupFilters,
    GroupSort,
    GroupSummary,
    GroupUpdate,
    JoinRequest,
    NextEvent,
)
from app.services.analytics import track
from app.services.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)

UPCOMING_EVENTS_IN_DETAIL = 5


def member_count_expr() -> Any:
    return (
        select(func.count(Membership.id))
        .where(
            Membership.group_id == StudentGroup.id,
            Membership.status == MembershipStatus.ACTIVE,
        )
        .correlate(StudentGroup)
        .scalar_subquery()
    )


def _next_event_at_expr(now: datetime) -> Any:
    return (
        select(func.min(Event.starts_at))
        .where(Event.group_id == StudentGroup.id, Event.starts_at > now, ~Event.is_cancelled)
        .correlate(StudentGroup)
        .scalar_subquery()
    )


# --- Search -------------------------------------------------------------------------


def search_groups(
    db: Session,
    user: User,
    filters: GroupFilters,
    limit: int,
    offset: int,
    context: ClientContext | None = None,
) -> tuple[list[GroupSummary], int]:
    now = datetime.now(UTC)
    member_count = member_count_expr().label("member_count")
    next_event_at = _next_event_at_expr(now).label("next_event_at")

    stmt = _apply_filters(select(StudentGroup.id), filters, now)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    ordered = _apply_filters(
        select(StudentGroup.id, member_count, next_event_at), filters, now
    ).order_by(*_order_by(filters.sort, member_count, next_event_at), StudentGroup.id)
    ids = list(db.scalars(ordered.limit(limit).offset(offset)))
    items = build_summaries(db, user, ids)

    # Record searches, not plain browsing; only the first page so paging isn't double counted.
    applied = filters.active_filter_names()
    if applied and offset == 0:
        track(
            db,
            "group_searched",
            user_id=user.id,
            context=context,
            screen="explore",
            properties={
                "query": filters.q,
                "filters": applied,
                "categories": filters.categories,
                "interest_ids": filters.interest_ids,
                "verified": filters.verified,
                "has_upcoming_events": filters.has_upcoming_events,
                "buildings": filters.buildings,
                "result_count": total,
                "result_group_ids": ids,
            },
        )
        db.commit()
    return items, total


def _apply_filters(stmt: Select, filters: GroupFilters, now: datetime) -> Select:
    stmt = stmt.where(StudentGroup.is_active, StudentGroup.review_status == ReviewStatus.APPROVED)
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        tag_match = exists().where(
            GroupInterest.group_id == StudentGroup.id,
            GroupInterest.interest_id == Interest.id,
            Interest.name.ilike(pattern),
        )
        stmt = stmt.where(
            or_(
                StudentGroup.name.ilike(pattern),
                StudentGroup.description.ilike(pattern),
                StudentGroup.category.has(Category.label.ilike(pattern)),
                tag_match,
            )
        )
    if filters.categories:
        stmt = stmt.where(StudentGroup.category.has(Category.slug.in_(filters.categories)))
    if filters.interest_ids:
        stmt = stmt.where(
            exists().where(
                GroupInterest.group_id == StudentGroup.id,
                GroupInterest.interest_id.in_(filters.interest_ids),
            )
        )
    if filters.verified is not None:
        stmt = stmt.where(StudentGroup.verified.is_(filters.verified))
    if filters.has_upcoming_events is not None:
        upcoming = exists().where(
            Event.group_id == StudentGroup.id, Event.starts_at > now, ~Event.is_cancelled
        )
        stmt = stmt.where(upcoming if filters.has_upcoming_events else ~upcoming)
    if filters.buildings:
        stmt = stmt.where(
            StudentGroup.meeting_building.has(CampusBuilding.code.in_(filters.buildings))
        )
    return stmt


def _order_by(sort: GroupSort, member_count: Any, next_event_at: Any) -> list[Any]:
    match sort:
        case GroupSort.NEWEST:
            return [StudentGroup.created_at.desc()]
        case GroupSort.NAME:
            return [StudentGroup.name]
        case GroupSort.UPCOMING:
            return [next_event_at.asc().nulls_last()]
        case _:
            return [member_count.desc()]


# --- Read models --------------------------------------------------------------------


def build_summaries(db: Session, user: User, group_ids: Sequence[int]) -> list[GroupSummary]:
    """Hydrate groups into summaries, preserving the order of ``group_ids``."""
    if not group_ids:
        return []
    now = datetime.now(UTC)
    groups = {
        g.id: g
        for g in db.scalars(
            select(StudentGroup)
            .where(StudentGroup.id.in_(group_ids))
            .options(selectinload(StudentGroup.category), selectinload(StudentGroup.tags))
        )
    }
    counts = dict(
        db.execute(
            select(Membership.group_id, func.count(Membership.id))
            .where(Membership.group_id.in_(group_ids), Membership.status == MembershipStatus.ACTIVE)
            .group_by(Membership.group_id)
        ).all()
    )
    next_events = {
        e.group_id: e
        for e in db.scalars(
            select(Event)
            .where(Event.group_id.in_(group_ids), Event.starts_at > now, ~Event.is_cancelled)
            .order_by(Event.group_id, Event.starts_at)
            .ext(distinct_on(Event.group_id))
        )
    }
    member_of = set(
        db.scalars(
            select(Membership.group_id).where(
                Membership.user_id == user.id,
                Membership.group_id.in_(group_ids),
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    )
    saved = set(
        db.scalars(
            select(GroupSave.group_id).where(
                GroupSave.user_id == user.id, GroupSave.group_id.in_(group_ids)
            )
        )
    )
    return [
        _summary(
            groups[gid], counts.get(gid, 0), next_events.get(gid), gid in member_of, gid in saved
        )
        for gid in group_ids
        if gid in groups
    ]


def _summary(
    group: StudentGroup,
    member_count: int,
    next_event: Event | None,
    is_member: bool,
    is_saved: bool,
) -> GroupSummary:
    return GroupSummary(
        id=group.id,
        name=group.name,
        category=CategoryRead.model_validate(group.category),
        description=group.description,
        color=group.color,
        image_url=group.image_url,
        verified=group.verified,
        is_active=group.is_active,
        review_status=group.review_status.value,
        rejection_reason=group.rejection_reason,
        member_count=member_count,
        tags=[TagRead.model_validate(t) for t in sorted(group.tags, key=lambda t: t.name)],
        next_event=(
            NextEvent(id=next_event.id, title=next_event.title, starts_at=next_event.starts_at)
            if next_event
            else None
        ),
        is_member=is_member,
        is_saved=is_saved,
    )


def get_group_detail(
    db: Session,
    user: User,
    group_id: int,
    entry_point: EntryPoint | None = None,
    rec_request_id: str | None = None,
    context: ClientContext | None = None,
) -> GroupDetail:
    """Group profile. Opening it is recorded as ``group_viewed`` (BQ4, BQ6, BQ7, BQ13)."""
    group = db.scalar(
        select(StudentGroup)
        .where(StudentGroup.id == group_id)
        .options(selectinload(StudentGroup.meeting_building))
    )
    if group is None or not is_visible_to(db, group, user):
        raise NotFoundError("Group not found")
    summary = build_summaries(db, user, [group_id])[0]
    upcoming = db.scalars(
        select(Event)
        .where(Event.group_id == group_id, Event.starts_at > datetime.now(UTC), ~Event.is_cancelled)
        .options(selectinload(Event.building))
        .order_by(Event.starts_at)
        .limit(UPCOMING_EVENTS_IN_DETAIL)
    ).all()
    membership = _active_membership(db, user.id, group_id)

    # Creators checking their own proposal are not Explore traffic: don't skew BQ4/6/7/13.
    if group.review_status == ReviewStatus.APPROVED:
        track(
            db,
            "group_viewed",
            user_id=user.id,
            context=context,
            screen="group_detail",
            properties={
                "group_id": group_id,
                "category": summary.category.slug,
                "entry_point": (entry_point or EntryPoint.DIRECT).value,
                "rec_request_id": rec_request_id,
                "is_member": summary.is_member,
                "profile": profile_completeness(group, has_upcoming_event=bool(upcoming)),
            },
        )
        db.commit()

    return GroupDetail(
        **summary.model_dump(),
        founded_year=group.founded_year,
        contact_email=group.contact_email,
        instagram_url=group.instagram_url,
        website_url=group.website_url,
        meeting_building=(
            BuildingRead.model_validate(group.meeting_building) if group.meeting_building else None
        ),
        upcoming_events=[EventSummary.model_validate(e) for e in upcoming],
        my_role=membership.role.value if membership else None,
        created_at=group.created_at,
    )


def profile_completeness(group: StudentGroup, has_upcoming_event: bool) -> dict[str, Any]:
    """Snapshot of which optional profile fields are filled in (BQ4)."""
    return {
        "has_image": group.image_url is not None,
        "has_contact_email": group.contact_email is not None,
        "has_instagram": group.instagram_url is not None,
        "has_website": group.website_url is not None,
        "has_meeting_building": group.meeting_building_id is not None,
        "has_founded_year": group.founded_year is not None,
        "has_upcoming_event": has_upcoming_event,
        "verified": group.verified,
        "description_length": len(group.description),
        "tag_count": len(group.tags),
    }


def list_user_groups(db: Session, user: User) -> list[GroupSummary]:
    ids = db.scalars(
        select(Membership.group_id)
        .where(Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE)
        .order_by(Membership.joined_at.desc())
    ).all()
    return build_summaries(db, user, ids)


def list_saved_groups(db: Session, user: User) -> list[GroupSummary]:
    ids = db.scalars(
        select(GroupSave.group_id)
        .where(GroupSave.user_id == user.id)
        .order_by(GroupSave.created_at.desc())
    ).all()
    return build_summaries(db, user, ids)


# --- Saves ---------------------------------------------------------------------------


def save_group(
    db: Session,
    user: User,
    group_id: int,
    context: ClientContext | None,
    source: str | None = None,
) -> None:
    """Bookmark a group. ``source`` is the screen it was saved from (e.g. "explore", BQ13)."""
    group = _get_group(db, group_id)
    if not is_visible_to(db, group, user):
        raise NotFoundError("Group not found")
    if db.get(GroupSave, (user.id, group_id)) is None:
        db.add(GroupSave(user_id=user.id, group_id=group_id))
        track(
            db,
            "group_saved",
            user_id=user.id,
            context=context,
            properties={
                "group_id": group_id,
                "category": group.category.slug,
                "source": source or "group_detail",
            },
        )
        db.commit()


def unsave_group(db: Session, user: User, group_id: int, context: ClientContext | None) -> None:
    deleted = db.execute(
        delete(GroupSave).where(GroupSave.user_id == user.id, GroupSave.group_id == group_id)
    ).rowcount
    if deleted:
        track(
            db, "group_unsaved", user_id=user.id, context=context, properties={"group_id": group_id}
        )
    db.commit()


# --- Membership ----------------------------------------------------------------------


def join_group(
    db: Session, user: User, group_id: int, request: JoinRequest, context: ClientContext | None
) -> Membership:
    """Submit the join form. Idempotent: joining a group you belong to is a no-op."""
    group = _get_group(db, group_id)
    if group.review_status != ReviewStatus.APPROVED:
        raise ConflictError("This group is not accepting members until it is approved")
    if not group.is_active:
        raise ConflictError("This group is not accepting members")

    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.group_id == group_id)
    )
    if membership is not None and membership.status == MembershipStatus.ACTIVE:
        return membership

    now = datetime.now(UTC)
    if membership is None:
        membership = Membership(user_id=user.id, group_id=group_id, role=MembershipRole.MEMBER)
        db.add(membership)
    membership.status = MembershipStatus.ACTIVE
    membership.joined_at = now
    membership.left_at = None
    membership.entry_point = request.entry_point

    track(
        db,
        "group_joined",
        user_id=user.id,
        context=context,
        screen="join_form",
        properties={
            "group_id": group_id,
            "category": group.category.slug,
            "entry_point": request.entry_point.value,
            "rec_request_id": str(request.rec_request_id) if request.rec_request_id else None,
            "join_attempt_id": request.join_attempt_id,
            "has_motivation": bool(request.motivation),
        },
    )
    db.commit()
    return membership


def leave_group(db: Session, user: User, group_id: int, context: ClientContext | None) -> None:
    membership = _active_membership(db, user.id, group_id)
    if membership is None:
        raise NotFoundError("You are not a member of this group")
    if membership.role == MembershipRole.ADMIN and _admin_count(db, group_id) == 1:
        raise ConflictError("The last admin cannot leave the group; promote another admin first")

    membership.status = MembershipStatus.LEFT
    membership.left_at = datetime.now(UTC)
    track(db, "group_left", user_id=user.id, context=context, properties={"group_id": group_id})
    db.commit()


# --- Create / update -----------------------------------------------------------------


def create_group(
    db: Session, user: User, data: GroupCreate, context: ClientContext | None
) -> StudentGroup:
    """Submit a group proposal. It stays ``pending`` (invisible to other students) until a
    platform admin approves it; the creator becomes its admin right away."""
    if db.scalar(select(StudentGroup.id).where(func.lower(StudentGroup.name) == data.name.lower())):
        raise ConflictError("A group with this name already exists")
    category = _resolve_category(db, data)
    _check_building(db, data.meeting_building_id)
    tags = (
        _load_tags(db, data.tag_ids)
        if data.tag_ids
        else fallback_tags(db, category.id, f"{data.name} {data.description}")
    )

    group = StudentGroup(
        name=data.name.strip(),
        category_id=category.id,
        description=data.description,
        color=data.color,
        image_url=_url(data.image_url),
        founded_year=data.founded_year,
        contact_email=data.contact_email,
        instagram_url=_url(data.instagram_url),
        website_url=_url(data.website_url),
        meeting_building_id=data.meeting_building_id,
        created_by_id=user.id,
        tags=tags,
        review_status=ReviewStatus.PENDING,
    )
    db.add(group)
    db.flush()
    db.add(
        Membership(
            user_id=user.id,
            group_id=group.id,
            role=MembershipRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
    )
    track(
        db,
        "group_created",
        user_id=user.id,
        context=context,
        properties={
            "group_id": group.id,
            "category_id": category.id,
            "tag_ids": [tag.id for tag in tags],
            "tags_inferred": not data.tag_ids,
        },
    )
    db.commit()
    return group


def update_group(db: Session, user: User, group_id: int, changes: GroupUpdate) -> StudentGroup:
    group = _get_group(db, group_id)
    require_group_admin(db, user, group_id)
    values = changes.model_dump(exclude_unset=True)
    if "meeting_building_id" in values:
        _check_building(db, values["meeting_building_id"])
    if (tag_ids := values.pop("tag_ids", None)) is not None:
        group.tags = _load_tags(db, tag_ids)
    for field, value in values.items():
        setattr(group, field, _url(value) if field.endswith("_url") else value)
    db.commit()
    return group


def require_group_admin(db: Session, user: User, group_id: int) -> None:
    membership = _active_membership(db, user.id, group_id)
    if membership is None or membership.role != MembershipRole.ADMIN:
        raise PermissionDeniedError("Only group admins can do this")


def require_group_member(db: Session, user: User, group_id: int) -> Membership:
    membership = _active_membership(db, user.id, group_id)
    if membership is None:
        raise PermissionDeniedError("Only group members can do this")
    return membership


# --- Visibility & tags ---------------------------------------------------------------

MAX_FALLBACK_TAGS = 3


def is_visible_to(db: Session, group: StudentGroup, user: User) -> bool:
    """Approved groups are public; pending/rejected ones only to their creator and admins."""
    if group.review_status == ReviewStatus.APPROVED:
        return True
    if group.created_by_id == user.id:
        return True
    membership = _active_membership(db, user.id, group.id)
    return membership is not None and membership.role == MembershipRole.ADMIN


def fallback_tags(db: Session, category_id: int, text: str) -> list[Interest]:
    """Tags for a group created without ``tag_ids``, so the recommender can match it.

    Interests of the group's category whose names appear in ``text`` (case- and
    accent-insensitive, whole words), in order of appearance, at most 3. If none
    appear, the category's interest with the most opted-in students.
    """
    interests = db.scalars(
        select(Interest).where(Interest.category_id == category_id).order_by(Interest.name)
    ).all()
    haystack = _normalize(text)
    found = []
    for interest in interests:
        pattern = rf"(?<!\w){re.escape(_normalize(interest.name))}(?!\w)"
        if match := re.search(pattern, haystack):
            found.append((match.start(), interest))
    if found:
        ordered = sorted(found, key=lambda pair: pair[0])
        return [interest for _, interest in ordered][:MAX_FALLBACK_TAGS]

    most_popular = db.scalar(
        select(Interest)
        .outerjoin(UserInterest, UserInterest.interest_id == Interest.id)
        .where(Interest.category_id == category_id)
        .group_by(Interest.id)
        .order_by(func.count(UserInterest.user_id).desc(), Interest.name)
        .limit(1)
    )
    return [most_popular] if most_popular else []


def _normalize(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return stripped.lower()


def _resolve_category(db: Session, data: GroupCreate) -> Category:
    if data.category is not None:
        category = db.scalar(select(Category).where(Category.slug == data.category.strip()))
        if category is None:
            raise ValidationFailedError(f"Unknown category {data.category!r}")
        return category
    category = db.get(Category, data.category_id)
    if category is None:
        raise ValidationFailedError("Unknown category_id")
    return category


# --- Helpers -------------------------------------------------------------------------


def _get_group(db: Session, group_id: int) -> StudentGroup:
    group = db.get(StudentGroup, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group


def _active_membership(db: Session, user_id: int, group_id: int) -> Membership | None:
    return db.scalar(
        select(Membership).where(
            and_(
                Membership.user_id == user_id,
                Membership.group_id == group_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    )


def _admin_count(db: Session, group_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Membership.id)).where(
                Membership.group_id == group_id,
                Membership.role == MembershipRole.ADMIN,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        or 0
    )


def _load_tags(db: Session, tag_ids: list[int]) -> list[Interest]:
    tags = db.scalars(select(Interest).where(Interest.id.in_(set(tag_ids)))).all()
    if len(tags) != len(set(tag_ids)):
        raise ValidationFailedError("Unknown tag ids")
    return list(tags)


def _check_building(db: Session, building_id: int | None) -> None:
    if building_id is not None and db.get(CampusBuilding, building_id) is None:
        raise ValidationFailedError("Unknown meeting_building_id")


def _url(value: object | None) -> str | None:
    return str(value) if value is not None else None
