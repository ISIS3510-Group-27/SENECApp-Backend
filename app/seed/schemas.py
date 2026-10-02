"""Pydantic schemas that validate the JSON fixtures before anything touches the DB."""

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class _Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CategoryFixture(_Fixture):
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    label: str
    icon: str | None = None


class InterestFixture(_Fixture):
    name: str
    category: str


class CampusBuildingFixture(_Fixture):
    code: str
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class StudentGroupFixture(_Fixture):
    name: str
    category: str
    description: str
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    image_url: HttpUrl | None = None
    verified: bool = False
    is_active: bool = True
    founded_year: int | None = Field(default=None, ge=1948, le=2100)
    contact_email: str | None = None
    instagram_url: HttpUrl | None = None
    website_url: HttpUrl | None = None
    meeting_building: str | None = None
    tags: list[str] = Field(default_factory=list)


class ReferenceFixtures(_Fixture):
    """All reference fixtures, with cross-file references checked."""

    categories: list[CategoryFixture]
    interests: list[InterestFixture]
    campus_buildings: list[CampusBuildingFixture]
    student_groups: list[StudentGroupFixture]

    @model_validator(mode="after")
    def check_references(self) -> "ReferenceFixtures":
        _ensure_unique("category slug", [c.slug for c in self.categories])
        _ensure_unique("interest name", [i.name for i in self.interests])
        _ensure_unique("building code", [b.code for b in self.campus_buildings])
        _ensure_unique("group name", [g.name for g in self.student_groups])

        category_slugs = {c.slug for c in self.categories}
        interest_names = {i.name for i in self.interests}
        building_codes = {b.code for b in self.campus_buildings}

        for interest in self.interests:
            if interest.category not in category_slugs:
                raise ValueError(f"Interest {interest.name!r}: unknown category")
        for group in self.student_groups:
            if group.category not in category_slugs:
                raise ValueError(f"Group {group.name!r}: unknown category {group.category!r}")
            if group.meeting_building and group.meeting_building not in building_codes:
                raise ValueError(
                    f"Group {group.name!r}: unknown building {group.meeting_building!r}"
                )
            unknown_tags = set(group.tags) - interest_names
            if unknown_tags:
                raise ValueError(f"Group {group.name!r}: unknown tags {sorted(unknown_tags)}")
        return self


def _ensure_unique(label: str, values: list[str]) -> None:
    duplicates = {v for v in values if values.count(v) > 1}
    if duplicates:
        raise ValueError(f"Duplicate {label}: {sorted(duplicates)}")
