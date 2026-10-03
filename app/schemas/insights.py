from datetime import datetime

from pydantic import BaseModel

from app.schemas.catalog import BuildingRead


class SuggestedSlot(BaseModel):
    weekday: int
    start_time: str
    end_time: str
    free_members: int
    free_ratio: float
    attendance_rate: float | None
    score: float
    next_starts_at: datetime


class BestTimes(BaseModel):
    members: int
    members_with_schedule: int
    past_events: int
    duration_minutes: int
    slots: list[SuggestedSlot]


class AudienceCell(BaseModel):
    hour: int | None
    building: BuildingRead | None
    impressions: int
    interactions: int
    interaction_rate: float | None


class Audience(BaseModel):
    question: str
    answer: str
    days: int
    best_time_and_place: list[AudienceCell]
    by_hour: list[AudienceCell]
    by_building: list[AudienceCell]
