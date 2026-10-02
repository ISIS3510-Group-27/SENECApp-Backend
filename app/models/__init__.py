"""ORM models.

Import every model module here so that Alembic autogenerate can discover all tables
through ``Base.metadata``.
"""

from app.db.base import Base
from app.models.campus import CampusBuilding
from app.models.event import Event
from app.models.group import (
    GroupInterest,
    GroupSave,
    Membership,
    MembershipRole,
    MembershipStatus,
    StudentGroup,
)
from app.models.interest import Category, Interest, UserInterest
from app.models.user import User

__all__ = [
    "Base",
    "CampusBuilding",
    "Category",
    "Event",
    "GroupInterest",
    "GroupSave",
    "Interest",
    "Membership",
    "MembershipRole",
    "MembershipStatus",
    "StudentGroup",
    "User",
    "UserInterest",
]
