from pydantic import BaseModel, ConfigDict


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    label: str
    icon: str | None


class InterestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    category: CategoryRead | None


class TagRead(BaseModel):
    """Compact interest used as a group tag."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str


class BuildingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    latitude: float
    longitude: float
