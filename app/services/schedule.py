"""Weekly class schedules and the free time between classes."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import CampusBuilding, ScheduleBlock, User
from app.schemas.schedule import ScheduleIn
from app.services.errors import ValidationFailedError

# Campus "day" used to compute free blocks.
DAY_START = time(7, 0)
DAY_END = time(21, 0)
# Gaps shorter than this are not worth suggesting anything for.
MIN_FREE_BLOCK = timedelta(minutes=30)


@dataclass(frozen=True)
class FreeBlock:
    starts_at: datetime
    ends_at: datetime
    # Building of the class right before this gap, if any (where the student probably is).
    previous_building_id: int | None
    # Building of the class right after this gap, if any.
    next_building_id: int | None

    @property
    def minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    def contains(self, moment: datetime) -> bool:
        return self.starts_at <= moment < self.ends_at


def campus_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().campus_timezone)


def get_schedule(db: Session, user_id: int) -> list[ScheduleBlock]:
    return list(
        db.scalars(
            select(ScheduleBlock)
            .where(ScheduleBlock.user_id == user_id)
            .options(selectinload(ScheduleBlock.building))
            .order_by(ScheduleBlock.weekday, ScheduleBlock.start_time)
        )
    )


def replace_schedule(db: Session, user: User, schedule: ScheduleIn) -> list[ScheduleBlock]:
    building_ids = {b.building_id for b in schedule.blocks if b.building_id is not None}
    if building_ids:
        known = set(
            db.scalars(select(CampusBuilding.id).where(CampusBuilding.id.in_(building_ids)))
        )
        if building_ids - known:
            raise ValidationFailedError("Unknown building_id in schedule")

    db.execute(delete(ScheduleBlock).where(ScheduleBlock.user_id == user.id))
    db.add_all(ScheduleBlock(user_id=user.id, **block.model_dump()) for block in schedule.blocks)
    db.commit()
    return get_schedule(db, user.id)


def free_blocks_for_day(
    blocks: Iterable[ScheduleBlock], day: date, tz: ZoneInfo | None = None
) -> list[FreeBlock]:
    """Gaps of at least ``MIN_FREE_BLOCK`` between ``DAY_START`` and ``DAY_END`` on ``day``."""
    tz = tz or campus_tz()
    classes: Sequence[ScheduleBlock] = sorted(
        (b for b in blocks if b.weekday == day.weekday()), key=lambda b: b.start_time
    )

    def at(clock: time) -> datetime:
        return datetime.combine(day, clock, tzinfo=tz)

    free: list[FreeBlock] = []
    cursor, previous_building = at(DAY_START), None
    for block in classes:
        start = at(block.start_time)
        if start - cursor >= MIN_FREE_BLOCK:
            free.append(FreeBlock(cursor, start, previous_building, block.building_id))
        cursor = max(cursor, at(block.end_time))
        previous_building = block.building_id
    if at(DAY_END) - cursor >= MIN_FREE_BLOCK:
        free.append(FreeBlock(cursor, at(DAY_END), previous_building, None))
    return free


def current_or_next_free_block(
    blocks: Iterable[ScheduleBlock], moment: datetime
) -> FreeBlock | None:
    """The free block containing ``moment``, else the next one later that same day."""
    tz = campus_tz()
    local = moment.astimezone(tz)
    for block in free_blocks_for_day(list(blocks), local.date(), tz):
        if block.contains(local):
            return FreeBlock(
                local, block.ends_at, block.previous_building_id, block.next_building_id
            )
        if block.starts_at > local:
            return block
    return None


def is_free_at(blocks: Iterable[ScheduleBlock], moment: datetime) -> bool:
    """Whether ``moment`` falls outside every class block (local campus time)."""
    local = moment.astimezone(campus_tz())
    return not any(
        b.weekday == local.weekday() and b.start_time <= local.time() < b.end_time for b in blocks
    )
