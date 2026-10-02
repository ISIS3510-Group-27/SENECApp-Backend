import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.catalog import BuildingRead
from app.schemas.event import EventRead
from app.schemas.group import GroupSummary


class GroupRecommendation(BaseModel):
    group: GroupSummary
    score: float
    reasons: list[str]


class GroupRecommendations(BaseModel):
    # Send this as ``rec_request_id`` (with ``entry_point=recommendation``) when the
    # student opens or joins one of these groups, so the outcome is attributed.
    request_id: uuid.UUID
    model_version: str
    items: list[GroupRecommendation]


class FreeBlockRead(BaseModel):
    starts_at: datetime
    ends_at: datetime
    minutes: int


class LocationContext(BaseModel):
    building: BuildingRead | None
    # "gps": from the phone; "schedule": building of the last/next class; "none": unknown.
    source: Literal["gps", "schedule", "none"]
    on_campus: bool | None


class EventSuggestion(BaseModel):
    event: EventRead
    distance_m: float | None
    walking_minutes: int | None
    score: float
    reasons: list[str]


class FreeNowSuggestions(BaseModel):
    request_id: uuid.UUID
    free_block: FreeBlockRead | None
    schedule_known: bool
    location: LocationContext
    items: list[EventSuggestion]
    message: str | None = None


class TrainingSummary(BaseModel):
    trained: bool
    version: str | None = None
    rows: int = 0
    auc: float | None = None
    positive_rate: float | None = None
    weights: dict[str, float] | None = None
    reason: str | None = None
