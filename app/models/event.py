from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.campus import CampusBuilding
    from app.models.group import StudentGroup


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

    group: Mapped[StudentGroup] = relationship(back_populates="events")
    building: Mapped[CampusBuilding | None] = relationship()
