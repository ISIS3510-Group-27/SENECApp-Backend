from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.models import CampusBuilding, Category, Interest
from app.schemas.catalog import BuildingRead, CategoryRead, InterestRead

router = APIRouter(tags=["catalog"])


@router.get("/categories")
def list_categories(db: DbSession) -> list[CategoryRead]:
    categories = db.scalars(select(Category).order_by(Category.id))
    return [CategoryRead.model_validate(c) for c in categories]


@router.get("/interests")
def list_interests(db: DbSession) -> list[InterestRead]:
    """All interests students can opt in to, used by onboarding and profile editing."""
    interests = db.scalars(
        select(Interest).options(selectinload(Interest.category)).order_by(Interest.name)
    )
    return [InterestRead.model_validate(i) for i in interests]


@router.get("/buildings")
def list_buildings(db: DbSession) -> list[BuildingRead]:
    """Campus buildings with coordinates (maps, meeting places, context-aware features)."""
    buildings = db.scalars(select(CampusBuilding).order_by(CampusBuilding.code))
    return [BuildingRead.model_validate(b) for b in buildings]
