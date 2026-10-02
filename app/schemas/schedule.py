from datetime import time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.catalog import BuildingRead


class ScheduleBlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")
    start_time: time
    end_time: time
    title: str | None = Field(default=None, max_length=150)
    building_id: int | None = None

    @model_validator(mode="after")
    def check_order(self) -> "ScheduleBlockIn":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ScheduleIn(BaseModel):
    """The student's full weekly class schedule (replaces the previous one)."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[ScheduleBlockIn] = Field(max_length=80)

    @model_validator(mode="after")
    def check_overlaps(self) -> "ScheduleIn":
        by_day = sorted(self.blocks, key=lambda b: (b.weekday, b.start_time))
        for previous, current in zip(by_day, by_day[1:], strict=False):
            if previous.weekday == current.weekday and current.start_time < previous.end_time:
                raise ValueError(f"Blocks overlap on weekday {current.weekday}")
        return self


class ScheduleBlockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weekday: int
    start_time: time
    end_time: time
    title: str | None
    building: BuildingRead | None
