from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.group import GroupSave, Membership
    from app.models.interest import UserInterest


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Null for seeded/simulated users that never signed in through Firebase.
    firebase_uid: Mapped[str | None] = mapped_column(String(128), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    program: Mapped[str | None] = mapped_column(String(150))
    semester: Mapped[int | None] = mapped_column(Integer)
    avatar_url: Mapped[str | None] = mapped_column(String(500))

    # Consent flags (Ley 1581 de 2012): features that use location or push
    # notifications must check these before collecting or sending anything.
    location_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notifications_opt_in: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    interests: Mapped[list[UserInterest]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    saves: Mapped[list[GroupSave]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
