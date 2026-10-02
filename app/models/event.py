from __future__ import annotations

import enum
import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import enum_check, str_enum
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.campus import CampusBuilding
    from app.models.group import StudentGroup


def new_check_in_code() -> str:
    return secrets.token_urlsafe(12)


class Event(TimestampMixin, Base):
    """An event hosted by a student group."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    building_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus_buildings.id", ondelete="SET NULL"), index=True
    )
    # Free-text detail for the place (room, meeting point, off-campus venue).
    location_detail: Mapped[str | None] = mapped_column(String(200))
    capacity: Mapped[int | None] = mapped_column(Integer)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Secret encoded in the QR code that organizers display for attendance check-in.
    check_in_code: Mapped[str] = mapped_column(String(32), default=new_check_in_code)

    group: Mapped[StudentGroup] = relationship(back_populates="events")
    building: Mapped[CampusBuilding | None] = relationship()


class CheckInMethod(enum.StrEnum):
    QR = "qr"
    MANUAL = "manual"


class Attendance(Base):
    """A student checked in to an event (business questions BQ10, BQ3)."""

    __tablename__ = "event_attendance"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id"),
        enum_check("method", CheckInMethod),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    method: Mapped[CheckInMethod] = mapped_column(str_enum(CheckInMethod))
    # Distance to the venue when the phone shared its location (raw coordinates are not stored).
    distance_m: Mapped[float | None] = mapped_column(Float)

    event: Mapped[Event] = relationship()
