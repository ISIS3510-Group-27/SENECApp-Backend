from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.catalog import BuildingRead


class EventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    building: BuildingRead | None
    location_detail: str | None
    is_cancelled: bool


class EventGroup(BaseModel):
    id: int
    name: str
    color: str | None


class EventRead(EventSummary):
    description: str | None
    capacity: int | None
    group: EventGroup
    attendee_count: int
    checked_in: bool


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    starts_at: datetime
    ends_at: datetime
    building_id: int | None = None
    location_detail: str | None = Field(default=None, max_length=200)
    capacity: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_times(self) -> "EventCreate":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("starts_at and ends_at must include a time zone offset")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    building_id: int | None = None
    location_detail: str | None = Field(default=None, max_length=200)
    capacity: int | None = Field(default=None, ge=1)
    is_cancelled: bool | None = None


class CheckInCode(BaseModel):
    event_id: int
    code: str
    # Encode this string in the QR code shown at the venue.
    qr_payload: str


class CheckInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    # Optional phone location; if sent, the student must be near the venue.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class CheckInResult(BaseModel):
    event_id: int
    checked_in_at: datetime
    distance_m: float | None
    already_checked_in: bool
