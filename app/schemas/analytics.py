import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EVENT_NAME_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"


class ClientEventIn(BaseModel):
    """One analytics event as sent by the mobile apps (see docs/event-taxonomy.md)."""

    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    name: str = Field(pattern=EVENT_NAME_PATTERN)
    occurred_at: datetime
    session_id: str | None = Field(default=None, max_length=64)
    screen: str | None = Field(default=None, max_length=64)
    app: Literal["flutter", "kotlin"] | None = None
    app_version: str | None = Field(default=None, max_length=32)
    platform: Literal["android", "ios"] | None = None
    device_model: str | None = Field(default=None, max_length=100)
    os_version: str | None = Field(default=None, max_length=50)
    properties: dict[str, Any] = Field(default_factory=dict, max_length=50)


class EventBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ClientEventIn] = Field(min_length=1, max_length=500)


class EventBatchResult(BaseModel):
    accepted: int
    duplicates: int
