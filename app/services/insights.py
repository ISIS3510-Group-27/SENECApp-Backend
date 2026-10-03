from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.questions import QUESTIONS, Params, bq3_free_block_event_interactions
from app.models import (
    Attendance,
    CampusBuilding,
    Event,
    Membership,
    MembershipStatus,
    ScheduleBlock,
    StudentGroup,
    User,
)
from app.schemas.catalog import BuildingRead
from app.schemas.insights import Audience, AudienceCell, BestTimes, SuggestedSlot
from app.services.errors import NotFoundError
from app.services.groups import require_group_admin
from app.services.schedule import DAY_END, DAY_START, campus_tz

STEP_MINUTES = 30
WEEKDAYS = range(6)
HISTORY_DAYS = 120
PRIOR_WEIGHT = 2.0
ATTENDANCE_WEIGHT = 0.25


def best_times(
    db: Session,
    user: User,
    group_id: int,
    duration_minutes: int,
    limit: int,
    now: datetime | None = None,
) -> BestTimes:
    _get_group(db, group_id)
    require_group_admin(db, user, group_id)
    now = now or datetime.now(UTC)

    member_ids = list(
        db.scalars(
            select(Membership.user_id).where(
                Membership.group_id == group_id, Membership.status == MembershipStatus.ACTIVE
            )
        )
    )
    blocks_by_user: dict[int, list[ScheduleBlock]] = defaultdict(list)
    if member_ids:
        for block in db.scalars(select(ScheduleBlock).where(ScheduleBlock.user_id.in_(member_ids))):
            blocks_by_user[block.user_id].append(block)

    rates, past_events = _attendance_rates(db, group_id, len(member_ids), now)
    with_schedule = len(blocks_by_user)
    result = BestTimes(
        members=len(member_ids),
        members_with_schedule=with_schedule,
        past_events=past_events,
        duration_minutes=duration_minutes,
        slots=[],
    )
    if with_schedule == 0:
        return result

    candidates = []
    last_start = _minutes(DAY_END) - duration_minutes
    for weekday in WEEKDAYS:
        for start in range(_minutes(DAY_START), last_start + 1, STEP_MINUTES):
            end = start + duration_minutes
            busy = sum(
                1
                for blocks in blocks_by_user.values()
                if any(
                    b.weekday == weekday
                    and _minutes(b.start_time) < end
                    and start < _minutes(b.end_time)
                    for b in blocks
                )
            )
            free = with_schedule - busy
            if free == 0:
                continue
            ratio = free / with_schedule
            attendance = rates(weekday, start // 60)
            boost = 1.0
            if attendance is not None and rates.best:
                boost = 1 - ATTENDANCE_WEIGHT + ATTENDANCE_WEIGHT * attendance / rates.best
            candidates.append((ratio * boost, weekday, start, free, ratio, attendance))

    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    chosen: list[tuple[float, int, int, int, float, float | None]] = []
    for candidate in candidates:
        _, weekday, start, *_ = candidate
        clashes = any(
            c[1] == weekday and start < c[2] + duration_minutes and c[2] < start + duration_minutes
            for c in chosen
        )
        if not clashes:
            chosen.append(candidate)
        if len(chosen) == limit:
            break

    result.slots = [
        SuggestedSlot(
            weekday=weekday,
            start_time=_clock(start),
            end_time=_clock(start + duration_minutes),
            free_members=free,
            free_ratio=round(ratio, 4),
            attendance_rate=None if attendance is None else round(attendance, 4),
            score=round(score, 4),
            next_starts_at=_next_occurrence(weekday, start, now),
        )
        for score, weekday, start, free, ratio, attendance in chosen
    ]
    return result


def audience(db: Session, user: User, group_id: int, days: int) -> Audience:
    _get_group(db, group_id)
    require_group_admin(db, user, group_id)
    until = datetime.now(UTC)
    data, answer = bq3_free_block_event_interactions(
        db, Params(since=until - timedelta(days=days), until=until)
    )
    buildings = {b.code: b for b in db.scalars(select(CampusBuilding))}

    def cell(row: dict) -> AudienceCell:
        building = buildings.get(row.get("building") or "")
        return AudienceCell(
            hour=row.get("hour"),
            building=BuildingRead.model_validate(building) if building else None,
            impressions=row["impressions"],
            interactions=row["interactions"],
            interaction_rate=row["interaction_rate"],
        )

    by_building = sorted(
        data["by_building"], key=lambda r: r["interaction_rate"] or 0, reverse=True
    )
    return Audience(
        question=QUESTIONS["3"].question,
        answer=answer,
        days=days,
        best_time_and_place=[cell(r) for r in data["best_time_and_place"] if r["building"]],
        by_hour=[cell(r) for r in data["by_hour"]],
        by_building=[cell(r) for r in by_building if r["building"] != "unknown"][:8],
    )


class _Rates:
    def __init__(self, events: list[tuple[datetime, float]]) -> None:
        self.overall = sum(r for _, r in events) / len(events) if events else None
        self._by_day: dict[int, list[float]] = defaultdict(list)
        self._by_hour: dict[int, list[float]] = defaultdict(list)
        for starts_at, rate in events:
            self._by_day[starts_at.weekday()].append(rate)
            self._by_hour[starts_at.hour].append(rate)
        self.best = max(
            (self(d, h) or 0 for d in WEEKDAYS for h in range(DAY_START.hour, DAY_END.hour)),
            default=0,
        )

    def _smooth(self, rates: list[float]) -> float:
        assert self.overall is not None
        return (sum(rates) + PRIOR_WEIGHT * self.overall) / (len(rates) + PRIOR_WEIGHT)

    def __call__(self, weekday: int, hour: int) -> float | None:
        if self.overall is None:
            return None
        return (self._smooth(self._by_day[weekday]) + self._smooth(self._by_hour[hour])) / 2


def _attendance_rates(
    db: Session, group_id: int, members: int, now: datetime
) -> tuple[_Rates, int]:
    if members == 0:
        return _Rates([]), 0
    rows = db.execute(
        select(Event.starts_at, func.count(Attendance.id))
        .outerjoin(Attendance, Attendance.event_id == Event.id)
        .where(
            Event.group_id == group_id,
            ~Event.is_cancelled,
            Event.ends_at < now,
            Event.starts_at >= now - timedelta(days=HISTORY_DAYS),
        )
        .group_by(Event.id)
    ).all()
    tz = campus_tz()
    events = [(starts_at.astimezone(tz), min(1.0, count / members)) for starts_at, count in rows]
    return _Rates(events), len(events)


def _get_group(db: Session, group_id: int) -> StudentGroup:
    group = db.get(StudentGroup, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group


def _minutes(clock: time) -> int:
    return clock.hour * 60 + clock.minute


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _next_occurrence(weekday: int, start: int, now: datetime) -> datetime:
    tz = campus_tz()
    local_now = now.astimezone(tz)
    day: date = local_now.date() + timedelta(days=(weekday - local_now.weekday()) % 7)
    moment = datetime.combine(day, time(start // 60, start % 60), tzinfo=tz)
    if moment <= local_now + timedelta(hours=1):
        moment += timedelta(days=7)
    return moment
