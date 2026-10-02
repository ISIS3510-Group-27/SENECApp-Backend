from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
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
from app.models.mixins import CreatedAtMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.campus import CampusBuilding
    from app.models.event import Event
    from app.models.interest import Category, Interest
    from app.models.user import User


class StudentGroup(TimestampMixin, Base):
    """A student organization (RSO).

    Optional profile fields (logo, links, meeting place...) are kept as separate
    columns on purpose: analytics correlate their presence with saves and joins.
    """

    __tablename__ = "student_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))
    color: Mapped[str | None] = mapped_column(String(9))  # hex, e.g. "#FF8A3D"
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    founded_year: Mapped[int | None] = mapped_column(Integer)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    instagram_url: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(500))
    meeting_building_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus_buildings.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    category: Mapped[Category] = relationship()
    meeting_building: Mapped[CampusBuilding | None] = relationship()
    tags: Mapped[list[Interest]] = relationship(secondary="group_interests")
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupInterest(Base):
    """Tags a group with an interest."""

    __tablename__ = "group_interests"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE"), primary_key=True
    )
    interest_id: Mapped[int] = mapped_column(
        ForeignKey("interests.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class MembershipRole(enum.StrEnum):
    MEMBER = "member"
    ADMIN = "admin"


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    LEFT = "left"


class EntryPoint(enum.StrEnum):
    """Where the student came from when they joined (business question BQ6)."""

    RECOMMENDATION = "recommendation"
    SEARCH = "search"
    EXPLORE = "explore"
    NOTIFICATION = "notification"
    EVENT = "event"
    DIRECT = "direct"


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id"),
        enum_check("role", MembershipRole),
        enum_check("status", MembershipStatus),
        enum_check("entry_point", EntryPoint),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MembershipRole] = mapped_column(
        str_enum(MembershipRole), default=MembershipRole.MEMBER
    )
    status: Mapped[MembershipStatus] = mapped_column(
        str_enum(MembershipStatus), default=MembershipStatus.ACTIVE
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Null for founders/admins created with the group.
    entry_point: Mapped[EntryPoint | None] = mapped_column(str_enum(EntryPoint))

    user: Mapped[User] = relationship(back_populates="memberships")
    group: Mapped[StudentGroup] = relationship(back_populates="memberships")


class GroupSave(CreatedAtMixin, Base):
    """A student bookmarking a group from Explore or its profile."""

    __tablename__ = "group_saves"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    user: Mapped[User] = relationship(back_populates="saves")
    group: Mapped[StudentGroup] = relationship()
