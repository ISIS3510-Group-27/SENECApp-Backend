"""ORM models.

Import every model module here so that Alembic autogenerate can discover all tables
through ``Base.metadata``.
"""

from app.db.base import Base
from app.models.analytics import AnalyticsEvent
from app.models.campus import CampusBuilding
from app.models.event import Attendance, CheckInMethod, Event
from app.models.group import (
    EntryPoint,
    GroupAdminInvite,
    GroupInterest,
    GroupSave,
    Membership,
    MembershipRole,
    MembershipStatus,
    ReviewStatus,
    StudentGroup,
)
from app.models.interest import Category, Interest, UserInterest
from app.models.messaging import (
    DeviceToken,
    GroupMessage,
    MessageKind,
    Notification,
    NotificationType,
    PushStatus,
)
from app.models.operations import (
    FeatureArea,
    JobRun,
    ReengagementArm,
    ReengagementCase,
    ReengagementStatus,
    Release,
    SeedRun,
)
from app.models.recommendation import (
    RecommendationKind,
    RecommendationLog,
    RecommenderModel,
)
from app.models.schedule import ScheduleBlock
from app.models.user import User

__all__ = [
    "AnalyticsEvent",
    "Attendance",
    "Base",
    "CampusBuilding",
    "Category",
    "CheckInMethod",
    "DeviceToken",
    "EntryPoint",
    "Event",
    "FeatureArea",
    "GroupAdminInvite",
    "GroupInterest",
    "GroupMessage",
    "GroupSave",
    "Interest",
    "JobRun",
    "Membership",
    "MembershipRole",
    "MembershipStatus",
    "MessageKind",
    "Notification",
    "NotificationType",
    "PushStatus",
    "RecommendationKind",
    "RecommendationLog",
    "RecommenderModel",
    "ReengagementArm",
    "ReengagementCase",
    "ReengagementStatus",
    "Release",
    "ReviewStatus",
    "ScheduleBlock",
    "SeedRun",
    "StudentGroup",
    "User",
    "UserInterest",
]
