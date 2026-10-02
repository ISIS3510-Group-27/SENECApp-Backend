"""Recommendations.

* Group recommendations (smart feature, BQ2): learned logistic-regression ranking
  over interests, schedule fit, proximity and popularity.
* "Free right now" event suggestions (context-aware feature, BQ3): uses the
  student's current free block (class schedule), time of day and location.

Every item shown is logged in ``recommendation_logs`` with its features.
"""

import math
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.client_context import ClientContext
from app.core.geo import haversine_m, nearest, walking_minutes
from app.models import (
    CampusBuilding,
    Event,
    GroupInterest,
    Interest,
    Membership,
    MembershipStatus,
    RecommendationKind,
    RecommendationLog,
    RecommenderModel,
    ScheduleBlock,
    StudentGroup,
    User,
    UserInterest,
)
from app.recommendations.model import (
    DEFAULT_MODEL_VERSION,
    DEFAULT_WEIGHTS,
    FEATURES,
    GroupProfile,
    StudentProfile,
    compute_features,
    explain,
    score,
    train_logistic_regression,
)
from app.schemas.catalog import BuildingRead
from app.schemas.recommendation import (
    EventSuggestion,
    FreeBlockRead,
    FreeNowSuggestions,
    GroupRecommendation,
    GroupRecommendations,
    LocationContext,
    TrainingSummary,
)
from app.services import events as event_service
from app.services import groups as group_service
from app.services import schedule as schedule_service

# Group events within this window describe when a group usually meets.
EVENT_WINDOW = timedelta(days=28)

# Free-now suggestions
ON_CAMPUS_RADIUS_M = 600
GPS_MATCH_RADIUS_M = 1_500
EVENT_PROXIMITY_SCALE_M = 300
MIN_MINUTES_AT_EVENT = 20
IN_PROGRESS_GRACE = timedelta(minutes=30)

# Training
TRAINING_WINDOW = timedelta(days=90)
LABEL_WINDOW = timedelta(days=14)
MIN_TRAINING_ROWS = 200


# --- Group recommendations -----------------------------------------------------------


def active_model(db: Session) -> tuple[str, dict[str, float]]:
    model = db.scalar(select(RecommenderModel).where(RecommenderModel.is_active))
    if model is None:
        return DEFAULT_MODEL_VERSION, DEFAULT_WEIGHTS
    return model.version, model.weights


def load_student_profile(db: Session, user_id: int) -> StudentProfile:
    interest_rows = db.execute(
        select(Interest.id, Interest.category_id)
        .join(UserInterest, UserInterest.interest_id == Interest.id)
        .where(UserInterest.user_id == user_id)
    ).all()
    blocks = list(
        db.scalars(
            select(ScheduleBlock)
            .where(ScheduleBlock.user_id == user_id)
            .options(selectinload(ScheduleBlock.building))
        )
    )
    locations = {
        (b.building.latitude, b.building.longitude) for b in blocks if b.building is not None
    }
    return StudentProfile(
        interest_ids=frozenset(row.id for row in interest_rows),
        category_counts=dict(Counter(row.category_id for row in interest_rows if row.category_id)),
        class_locations=tuple(sorted(locations)),
        is_free_at=(lambda moment: schedule_service.is_free_at(blocks, moment)) if blocks else None,
    )


def load_group_profiles(
    db: Session, group_ids: list[int] | None = None, now: datetime | None = None
) -> dict[int, GroupProfile]:
    now = now or datetime.now(UTC)
    stmt = (
        select(StudentGroup)
        .where(StudentGroup.is_active)
        .options(selectinload(StudentGroup.tags), selectinload(StudentGroup.meeting_building))
    )
    if group_ids is not None:
        stmt = stmt.where(StudentGroup.id.in_(group_ids))
    groups = db.scalars(stmt).all()
    ids = [g.id for g in groups]

    event_times: dict[int, list[datetime]] = defaultdict(list)
    for group_id, starts_at in db.execute(
        select(Event.group_id, Event.starts_at).where(
            Event.group_id.in_(ids),
            ~Event.is_cancelled,
            Event.starts_at.between(now - EVENT_WINDOW, now + EVENT_WINDOW),
        )
    ):
        event_times[group_id].append(starts_at)
    counts = dict(
        db.execute(
            select(Membership.group_id, func.count(Membership.id))
            .where(Membership.group_id.in_(ids), Membership.status == MembershipStatus.ACTIVE)
            .group_by(Membership.group_id)
        ).all()
    )
    return {
        g.id: GroupProfile(
            group_id=g.id,
            category_id=g.category_id,
            tag_ids=frozenset(t.id for t in g.tags),
            tag_names={t.id: t.name for t in g.tags},
            meeting_location=(
                (g.meeting_building.latitude, g.meeting_building.longitude)
                if g.meeting_building
                else None
            ),
            event_times=tuple(event_times.get(g.id, [])),
            member_count=counts.get(g.id, 0),
            verified=g.verified,
        )
        for g in groups
    }


def recommend_groups(
    db: Session, user: User, limit: int, context: ClientContext | None = None
) -> GroupRecommendations:
    version, weights = active_model(db)
    student = load_student_profile(db, user.id)
    joined = set(
        db.scalars(
            select(Membership.group_id).where(
                Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE
            )
        )
    )
    profiles = load_group_profiles(db)
    max_members = max((p.member_count for p in profiles.values()), default=0)

    ranked = []
    for profile in profiles.values():
        if profile.group_id in joined:
            continue
        features = compute_features(student, profile, max_members)
        ranked.append((score(features, weights), profile, features))
    ranked.sort(key=lambda item: (-item[0], item[1].group_id))
    top = ranked[:limit]

    request_id = uuid.uuid4()
    db.add_all(
        RecommendationLog(
            request_id=request_id,
            user_id=user.id,
            kind=RecommendationKind.GROUP,
            item_id=profile.group_id,
            rank=rank,
            score=round(value, 4),
            features=features,
            context={"app": context.app if context else None},
            model_version=version,
        )
        for rank, (value, profile, features) in enumerate(top, start=1)
    )
    db.commit()

    summaries = {
        s.id: s for s in group_service.build_summaries(db, user, [p.group_id for _, p, _ in top])
    }
    return GroupRecommendations(
        request_id=request_id,
        model_version=version,
        items=[
            GroupRecommendation(
                group=summaries[profile.group_id],
                score=round(value, 4),
                reasons=explain(features, profile, student),
            )
            for value, profile, features in top
        ],
    )


# --- Free-now event suggestions (context-aware) --------------------------------------


def free_now_suggestions(
    db: Session,
    user: User,
    *,
    latitude: float | None,
    longitude: float | None,
    at: datetime | None,
    limit: int,
) -> FreeNowSuggestions:
    now = (at or datetime.now(UTC)).astimezone(UTC)
    request_id = uuid.uuid4()
    blocks = schedule_service.get_schedule(db, user.id)
    schedule_known = bool(blocks)

    if schedule_known:
        free = schedule_service.current_or_next_free_block(blocks, now)
    else:  # no schedule: assume the student is free for the next two hours
        free = schedule_service.FreeBlock(now, now + timedelta(hours=2), None, None)

    buildings = list(db.scalars(select(CampusBuilding)))
    location = _resolve_location(user, latitude, longitude, free, buildings)
    if free is None:
        return FreeNowSuggestions(
            request_id=request_id,
            free_block=None,
            schedule_known=schedule_known,
            location=location,
            items=[],
            message="No more free time between classes today.",
        )

    window_end = free.ends_at - timedelta(minutes=MIN_MINUTES_AT_EVENT)
    events = db.scalars(
        select(Event)
        .where(
            ~Event.is_cancelled,
            Event.building_id.is_not(None),
            Event.starts_at >= free.starts_at - IN_PROGRESS_GRACE,
            Event.starts_at <= window_end,
            Event.ends_at > now,
        )
        .options(selectinload(Event.building))
    ).all()

    interest_ids = set(
        db.scalars(select(UserInterest.interest_id).where(UserInterest.user_id == user.id))
    )
    tags_by_group: dict[int, set[int]] = defaultdict(set)
    for group_id, interest_id in db.execute(
        select(GroupInterest.group_id, GroupInterest.interest_id).where(
            GroupInterest.group_id.in_({e.group_id for e in events})
        )
    ):
        tags_by_group[group_id].add(interest_id)
    member_of = set(
        db.scalars(
            select(Membership.group_id).where(
                Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE
            )
        )
    )

    origin = (
        (location.building.latitude, location.building.longitude) if location.building else None
    )
    scored = []
    for event in events:
        distance = (
            haversine_m(*origin, event.building.latitude, event.building.longitude)
            if origin
            else None
        )
        tags = tags_by_group.get(event.group_id, set())
        interest_match = len(tags & interest_ids) / len(tags) if tags else 0.0
        proximity = math.exp(-distance / EVENT_PROXIMITY_SCALE_M) if distance is not None else 0.5
        minutes_until = max(0.0, (event.starts_at - now).total_seconds() / 60)
        soon = math.exp(-minutes_until / 90)
        is_member = event.group_id in member_of
        value = 0.45 * interest_match + 0.35 * proximity + 0.2 * soon + (0.15 if is_member else 0)
        features = {
            "distance_m": round(distance, 1) if distance is not None else None,
            "interest_match": round(interest_match, 4),
            "minutes_until_start": round(minutes_until),
            "is_member": is_member,
        }
        scored.append((round(value, 4), event, features))
    scored.sort(key=lambda item: (-item[0], item[1].starts_at))
    top = scored[:limit]

    local_now = now.astimezone(schedule_service.campus_tz())
    log_context = {
        "weekday": local_now.weekday(),
        "hour": local_now.hour,
        "building_code": location.building.code if location.building else None,
        "location_source": location.source,
        "on_campus": location.on_campus,
        "free_block_minutes": free.minutes,
        "schedule_known": schedule_known,
    }
    db.add_all(
        RecommendationLog(
            request_id=request_id,
            user_id=user.id,
            kind=RecommendationKind.EVENT,
            item_id=event.id,
            rank=rank,
            score=value,
            features=features,
            context=log_context,
            model_version="free-now-v1",
        )
        for rank, (value, event, features) in enumerate(top, start=1)
    )
    db.commit()

    reads = {e.id: e for e in event_service.build_event_reads(db, user, [e.id for _, e, _ in top])}
    return FreeNowSuggestions(
        request_id=request_id,
        free_block=FreeBlockRead(
            starts_at=free.starts_at, ends_at=free.ends_at, minutes=free.minutes
        ),
        schedule_known=schedule_known,
        location=location,
        items=[
            EventSuggestion(
                event=reads[event.id],
                distance_m=features["distance_m"],
                walking_minutes=(
                    walking_minutes(features["distance_m"])
                    if features["distance_m"] is not None
                    else None
                ),
                score=value,
                reasons=_event_reasons(features, event, free),
            )
            for value, event, features in top
        ],
    )


def _resolve_location(
    user: User,
    latitude: float | None,
    longitude: float | None,
    free: schedule_service.FreeBlock | None,
    buildings: list[CampusBuilding],
) -> LocationContext:
    """Where is the student? GPS (only with consent), else their last/next class building."""
    if user.location_opt_in and latitude is not None and longitude is not None:
        match = nearest(latitude, longitude, buildings, max_distance_m=GPS_MATCH_RADIUS_M)
        if match:
            building, distance = match
            return LocationContext(
                building=BuildingRead.model_validate(building),
                source="gps",
                on_campus=distance <= ON_CAMPUS_RADIUS_M,
            )
        return LocationContext(building=None, source="gps", on_campus=False)

    building_id = (free.previous_building_id or free.next_building_id) if free else None
    building = next((b for b in buildings if b.id == building_id), None)
    if building is not None:
        return LocationContext(
            building=BuildingRead.model_validate(building), source="schedule", on_campus=None
        )
    return LocationContext(building=None, source="none", on_campus=None)


def _event_reasons(
    features: dict[str, Any], event: Event, free: schedule_service.FreeBlock
) -> list[str]:
    reasons = []
    if features["distance_m"] is not None:
        reasons.append(f"{walking_minutes(features['distance_m'])} min walk")
    if event.ends_at <= free.ends_at:
        reasons.append("Fits entirely in your free time")
    if features["interest_match"] > 0:
        reasons.append("Matches your interests")
    if features["is_member"]:
        reasons.append("From one of your groups")
    return reasons


# --- Training --------------------------------------------------------------------------


def train_group_model(db: Session, now: datetime | None = None) -> TrainingSummary:
    """Re-fit the group recommender on recent logs; activate it if training succeeds.

    Label: the student joined the recommended group within ``LABEL_WINDOW``.
    """
    now = now or datetime.now(UTC)
    joined = exists().where(
        and_(
            Membership.user_id == RecommendationLog.user_id,
            Membership.group_id == RecommendationLog.item_id,
            Membership.joined_at >= RecommendationLog.created_at,
            Membership.joined_at <= RecommendationLog.created_at + LABEL_WINDOW,
        )
    )
    rows = db.execute(
        select(RecommendationLog.features, joined.label("joined")).where(
            RecommendationLog.kind == RecommendationKind.GROUP,
            RecommendationLog.created_at >= now - TRAINING_WINDOW,
            RecommendationLog.created_at <= now - timedelta(days=1),
        )
    ).all()
    dataset = [
        ({name: float(features.get(name, 0.0)) for name in FEATURES}, int(label))
        for features, label in rows
    ]
    positives = sum(label for _, label in dataset)
    if len(dataset) < MIN_TRAINING_ROWS or positives in (0, len(dataset)):
        return TrainingSummary(
            trained=False,
            rows=len(dataset),
            reason=f"Need at least {MIN_TRAINING_ROWS} logged recommendations with both outcomes",
        )

    result = train_logistic_regression(dataset)
    version = f"lr-{now:%Y%m%d%H%M%S}"
    db.execute(update(RecommenderModel).values(is_active=False))
    db.add(
        RecommenderModel(
            version=version,
            weights=result.weights,
            metrics={"auc": result.auc, "rows": result.rows, "positive_rate": result.positive_rate},
            trained_at=now,
            is_active=True,
        )
    )
    db.commit()
    return TrainingSummary(
        trained=True,
        version=version,
        rows=result.rows,
        auc=result.auc,
        positive_rate=result.positive_rate,
        weights=result.weights,
    )
