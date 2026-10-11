from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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


class GroupAdminUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str


class GroupAdminsRead(BaseModel):
    group_id: int
    admins: list[GroupAdminUser]
    # Emails without an account yet: they become admins on their first sign-in.
    pending_emails: list[str]


class AddGroupAdmin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class GroupStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class AdminGroupRead(BaseModel):
    """Any group, whatever its status (platform admins)."""

    id: int
    name: str
    category: str
    review_status: str
    is_active: bool
    member_count: int
    admin_emails: list[str]
    pending_admin_emails: list[str]
    # Listed in app/seed/fixtures/student_groups.json (re-created on every start).
    from_catalog: bool


class BuildingIn(BaseModel):
    """A campus building (platform admins)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class BuildingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=150)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
