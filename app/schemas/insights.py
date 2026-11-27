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


class ProfileField(BaseModel):
    field: str
    filled: bool
    views: int
    correlation: float | None
    lift: float | None
    conversion_with: float | None
    conversion_without: float | None


class ProfileTips(BaseModel):
    question: str
    answer: str
    days: int
    filled: int
    total: int
    fields: list[ProfileField]


class HealthWeek(BaseModel):
    week_start: datetime
    attendance: int


class HealthCase(BaseModel):
    arm: str
    status: str
    detected_at: datetime
    lapsed_members: int
    returned_members: int
    return_rate: float | None


class ReengagementRate(BaseModel):
    arm: str
    groups: int
    lapsed: int
    returned: int
    return_to_attendance_rate: float | None


class GroupHealth(BaseModel):
    question: str
    answer: str
    weeks: list[HealthWeek]
    baseline: float
    recent: float
    decline_pct: float
    declining: bool
    case: HealthCase | None
    arms: list[ReengagementRate]


class JoinSource(BaseModel):
    entry_point: str
    views: int
    joins: int
    view_to_join_rate: float | None
    share_of_joins: float | None
    campus_view_to_join_rate: float | None


class JoinSources(BaseModel):
    question: str
    answer: str
    campus_answer: str
    days: int
    views: int
    joins: int
    sources: list[JoinSource]
