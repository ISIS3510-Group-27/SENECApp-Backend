from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import FeatureArea


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["flutter", "kotlin"]
    version: str = Field(min_length=1, max_length=32)
    released_at: datetime
    feature_area: FeatureArea
    notes: str | None = Field(default=None, max_length=2000)


class ReleaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app: str
    version: str
    released_at: datetime
    feature_area: str
    notes: str | None


class JobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    details: dict[str, Any]


class JobInfo(BaseModel):
    name: str
    description: str
    schedule: dict[str, Any]
    last_run: JobRunRead | None


class RecommenderModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: str
    weights: dict[str, float]
    metrics: dict[str, Any]
    trained_at: datetime
    is_active: bool
