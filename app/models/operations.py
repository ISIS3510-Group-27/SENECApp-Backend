import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import enum_check, str_enum
from app.models.mixins import CreatedAtMixin


class FeatureArea(enum.StrEnum):
    RECOMMENDATIONS = "recommendations"
    NOTIFICATIONS = "notifications"
    OTHER = "other"


class Release(CreatedAtMixin, Base):
    """A mobile app release and the feature area it changed (BQ14)."""

    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint("app", "version"),
        enum_check("feature_area", FeatureArea),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    app: Mapped[str] = mapped_column(String(20))  # "flutter" | "kotlin"
    version: Mapped[str] = mapped_column(String(32))
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feature_area: Mapped[FeatureArea] = mapped_column(str_enum(FeatureArea))
    notes: Mapped[str | None] = mapped_column(Text)


class ReengagementArm(enum.StrEnum):
    EVENT_REMINDERS = "event_reminders"
    GROUP_MESSAGES = "group_messages"


class ReengagementStatus(enum.StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class ReengagementCase(CreatedAtMixin, Base):
    """A group detected with declining attendance and the re-engagement feature
    randomly assigned to it (BQ10 experiment)."""

    __tablename__ = "reengagement_cases"
    __table_args__ = (
        enum_check("arm", ReengagementArm),
        enum_check("status", ReengagementStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE"), index=True
    )
    arm: Mapped[ReengagementArm] = mapped_column(str_enum(ReengagementArm))
    status: Mapped[ReengagementStatus] = mapped_column(
        str_enum(ReengagementStatus), default=ReengagementStatus.ACTIVE
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluation_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Weekly attendance oldest -> newest over the 8 weeks used for detection.
    weekly_attendance: Mapped[list[int]] = mapped_column(JSONB)
    baseline_weekly_attendance: Mapped[float] = mapped_column(Float)
    decline_pct: Mapped[float] = mapped_column(Float)
    # Members who attended in the baseline weeks but not in the declining weeks.
    lapsed_user_ids: Mapped[list[int]] = mapped_column(JSONB)
    returned_count: Mapped[int | None] = mapped_column(Integer)
    return_rate: Mapped[float | None] = mapped_column(Float)
    last_intervention_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobRun(Base):
    """Execution log of background jobs."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(60), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))  # "running" | "succeeded" | "failed"
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class SeedRun(Base):
    """Marks one-off seed steps (e.g. the simulation) as done so restarts skip them."""

    __tablename__ = "seed_runs"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
