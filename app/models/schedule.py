from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.campus import CampusBuilding


class ScheduleBlock(Base):
    """A recurring weekly class block. Free time is everything between blocks.

    Times are local campus time (``CAMPUS_TIMEZONE``).
    """

    __tablename__ = "schedule_blocks"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="time_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)  # 0 = Monday ... 6 = Sunday
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    title: Mapped[str | None] = mapped_column(String(150))
    building_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus_buildings.id", ondelete="SET NULL")
    )

    building: Mapped[CampusBuilding | None] = relationship()
