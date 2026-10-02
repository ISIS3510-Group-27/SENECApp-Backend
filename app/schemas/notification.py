from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Page


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    body: str
    data: dict[str, Any]
    group_id: int | None
    event_id: int | None
    created_at: datetime
    opened_at: datetime | None
    dismissed_at: datetime | None


class NotificationPage(Page[NotificationRead]):
    unread_count: int


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=10, max_length=512, description="FCM registration token")
    platform: Literal["android", "ios"] | None = None
    app: Literal["flutter", "kotlin"] | None = None
