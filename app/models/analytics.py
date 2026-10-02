import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalyticsEvent(Base):
    """Append-only log of everything the analytics pipeline consumes.

    ``source="client"`` rows are sent by the mobile apps in batches (screen views,
    errors, UI interactions); ``source="server"`` rows are recorded by the API
    itself for domain actions (searches, views, saves, joins...). Both share one
    envelope so the business-question queries can treat them uniformly.
    """

    __tablename__ = "analytics_events"
    __table_args__ = (Index("ix_analytics_events_name_occurred_at", "name", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Client-generated UUID: makes retried batches idempotent.
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(10))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    app: Mapped[str | None] = mapped_column(String(20))
    app_version: Mapped[str | None] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(20))
    device_model: Mapped[str | None] = mapped_column(String(100))
    os_version: Mapped[str | None] = mapped_column(String(50))
    screen: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
