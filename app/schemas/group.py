import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, model_validator

from app.models import EntryPoint
from app.schemas.catalog import BuildingRead, CategoryRead, TagRead
from app.schemas.event import EventSummary


class GroupSort(StrEnum):
    POPULAR = "popular"
    NEWEST = "newest"
    NAME = "name"
    UPCOMING = "upcoming"


class GroupFilters(BaseModel):
    """Explore/search filters. Every filter that is set narrows the results."""

    q: str | None = Field(default=None, max_length=100)
    categories: list[str] = Field(default_factory=list)
    interest_ids: list[int] = Field(default_factory=list)
    verified: bool | None = None
    has_upcoming_events: bool | None = None
    buildings: list[str] = Field(default_factory=list)
    sort: GroupSort = GroupSort.POPULAR

    def active_filter_names(self) -> list[str]:
        """Names of the filters actually applied (analytics, BQ12)."""
        candidates = {
            "q": self.q,
            "categories": self.categories,
            "interest_ids": self.interest_ids,
            "verified": self.verified is not None,
            "has_upcoming_events": self.has_upcoming_events is not None,
            "buildings": self.buildings,
        }
        return [name for name, value in candidates.items() if value]


class NextEvent(BaseModel):
    id: int
    title: str
    starts_at: datetime


class GroupSummary(BaseModel):
    id: int
    name: str
    category: CategoryRead
    description: str
    color: str | None
    image_url: str | None
    verified: bool
    is_active: bool
    # "pending" | "approved" | "rejected". Only the creator ever sees non-approved groups.
    review_status: str
    rejection_reason: str | None
    member_count: int
    tags: list[TagRead]
    next_event: NextEvent | None
    is_member: bool
    is_saved: bool


class GroupDetail(GroupSummary):
    founded_year: int | None
    contact_email: str | None
    instagram_url: str | None
    website_url: str | None
    meeting_building: BuildingRead | None
    upcoming_events: list[EventSummary]
    my_role: str | None
    # "pending" while the student's own request to become admin waits for an answer.
    admin_request_status: str | None = None
    # For the group's admins: how many members are waiting for an answer.
    pending_admin_requests: int | None = None
    created_at: datetime


class GroupCreate(BaseModel):
    """A group proposal. It is created as ``pending`` until Student Affairs approves it.

    Give the category either as ``category_id`` or as ``category`` (its slug), not both.
    Without ``tag_ids``, up to 3 tags are inferred from the name and description.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=150)
    category_id: int | None = None
    category: str | None = Field(default=None, min_length=1, max_length=50)
    description: str = Field(min_length=20, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    image_url: HttpUrl | None = None
    founded_year: int | None = Field(default=None, ge=1948, le=2100)
    contact_email: EmailStr | None = None
    instagram_url: HttpUrl | None = None
    website_url: HttpUrl | None = None
    meeting_building_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def exactly_one_category(self) -> "GroupCreate":
        if (self.category_id is None) == (self.category is None):
            raise ValueError("Send exactly one of category_id or category")
        return self


class GroupUpdate(BaseModel):
    """Partial update by a group admin: only the fields sent are changed."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=20, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    image_url: HttpUrl | None = None
    contact_email: EmailStr | None = None
    instagram_url: HttpUrl | None = None
    website_url: HttpUrl | None = None
    meeting_building_id: int | None = None
    tag_ids: list[int] | None = Field(default=None, min_length=1, max_length=8)
    is_active: bool | None = None


class JoinRequest(BaseModel):
    """Submission of the join form."""

    model_config = ConfigDict(extra="forbid")

    entry_point: EntryPoint = EntryPoint.DIRECT
    # Links the join to the recommendation list it came from (BQ2/BQ6).
    rec_request_id: uuid.UUID | None = None
    # Client-generated id shared by join_form_opened and the submission (BQ7 funnel).
    join_attempt_id: str | None = Field(default=None, max_length=64)
    motivation: str | None = Field(default=None, max_length=500)


class MembershipRead(BaseModel):
    group_id: int
    role: str
    status: str
    joined_at: datetime
    entry_point: str | None


class GroupCreator(BaseModel):
    id: int
    full_name: str
    email: str


class PendingGroupRead(BaseModel):
    """A group proposal waiting for review (platform admins)."""

    id: int
    name: str
    category: CategoryRead
    description: str
    contact_email: str | None
    tags: list[TagRead]
    creator: GroupCreator | None
    created_at: datetime


class GroupReviewResult(BaseModel):
    id: int
    name: str
    review_status: str
    rejection_reason: str | None
    reviewed_at: datetime


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class AdminRequestIn(BaseModel):
    """A member asking to become an admin of the group."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=300)


class AdminRequestRead(BaseModel):
    id: int
    group_id: int
    requester: GroupCreator
    note: str | None
    status: str
    created_at: datetime
    decided_at: datetime | None
