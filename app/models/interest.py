from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User


class Category(Base):
    """Top-level discovery category (Sports, Technology, Arts, ...)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str | None] = mapped_column(String(50))

    interests: Mapped[list[Interest]] = relationship(back_populates="category")


class Interest(Base):
    """Fine-grained interest/tag (Tennis, AI/ML, Startups, ...).

    Shared vocabulary between student profiles and group tags, which is what lets
    the analytics compare demand (students) against supply (groups) per interest.
    """

    __tablename__ = "interests"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )

    category: Mapped[Category | None] = relationship(back_populates="interests")


class UserInterest(CreatedAtMixin, Base):
    """A student opting in to an interest. ``created_at`` is the opt-in time."""

    __tablename__ = "user_interests"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    interest_id: Mapped[int] = mapped_column(
        ForeignKey("interests.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    user: Mapped[User] = relationship(back_populates="interests")
    interest: Mapped[Interest] = relationship()
