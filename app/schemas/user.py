from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models import User
from app.schemas.catalog import InterestRead


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str
    program: str | None
    semester: int | None
    avatar_url: str | None
    location_opt_in: bool
    notifications_opt_in: bool
    interests: list[InterestRead]
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserRead":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            program=user.program,
            semester=user.semester,
            avatar_url=user.avatar_url,
            location_opt_in=user.location_opt_in,
            notifications_opt_in=user.notifications_opt_in,
            interests=[
                InterestRead.model_validate(link.interest)
                for link in sorted(user.interests, key=lambda link: link.interest.name)
            ],
            created_at=user.created_at,
        )


class UserUpdate(BaseModel):
    """Partial profile update: only the fields sent are changed."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    program: str | None = Field(default=None, max_length=150)
    semester: int | None = Field(default=None, ge=1, le=20)
    avatar_url: HttpUrl | None = None
    location_opt_in: bool | None = None
    notifications_opt_in: bool | None = None


class InterestsUpdate(BaseModel):
    """Replaces the student's interests with exactly this set."""

    model_config = ConfigDict(extra="forbid")

    interest_ids: list[int] = Field(max_length=20)
