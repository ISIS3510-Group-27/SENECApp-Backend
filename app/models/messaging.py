import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import enum_check, str_enum
from app.models.mixins import CreatedAtMixin


class MessageKind(enum.StrEnum):
    USER = "user"
    # Posted by the platform, e.g. re-engagement messages (BQ10).
    SYSTEM = "system"


class GroupMessage(CreatedAtMixin, Base):
    """A message in a group's chat, visible to its members."""

    __tablename__ = "group_messages"
    __table_args__ = (
        Index("ix_group_messages_group_id_created_at", "group_id", "created_at"),
        enum_check("kind", MessageKind),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("student_groups.id", ondelete="CASCADE"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[MessageKind] = mapped_column(str_enum(MessageKind), default=MessageKind.USER)


class DeviceToken(CreatedAtMixin, Base):
    """An FCM registration token of one of the user's devices."""

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(512), unique=True)
    platform: Mapped[str | None] = mapped_column(String(20))
    app: Mapped[str | None] = mapped_column(String(20))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationType(enum.StrEnum):
    # The three notification families compared in BQ8.
    NEW_EVENT = "new_event"
    GROUP_RECOMMENDATION = "group_recommendation"
    GROUP_MESSAGE = "group_message"
    # Re-engagement feature compared in BQ10.
    EVENT_REMINDER = "event_reminder"
    # Outcome of a group proposal, sent to its creator (not part of BQ8).
    GROUP_REVIEW = "group_review"


class PushStatus(enum.StrEnum):
    SENT = "sent"
    FAILED = "failed"
    # No device registered or push delivery disabled; the notification is in-app only.
    SKIPPED = "skipped"


class Notification(CreatedAtMixin, Base):
    """A notification delivered to one user (in-app inbox, plus push when possible)."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
        Index("ix_notifications_type_created_at", "type", "created_at"),
        enum_check("type", NotificationType),
        enum_check("push_status", PushStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[NotificationType] = mapped_column(str_enum(NotificationType, length=30))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE")
    )
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    push_status: Mapped[PushStatus] = mapped_column(
        str_enum(PushStatus), default=PushStatus.SKIPPED
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
