"""Campus buildings managed by platform admins (on top of the fixture catalog).

Buildings listed in ``app/seed/fixtures/campus_buildings.json`` are re-applied on
every start, so they can only be changed in that file. Others can be added,
edited and deleted through the admin API.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CampusBuilding
from app.schemas.admin import BuildingIn, BuildingUpdate
from app.services.errors import ConflictError, NotFoundError

FIXTURE_HINT = (
    "This building is in app/seed/fixtures/campus_buildings.json and is re-applied on "
    "every start. Change it in that file and redeploy."
)


def catalog_codes() -> set[str]:
    from app.seed.loader import load_reference_fixtures

    return {b.code.upper() for b in load_reference_fixtures().campus_buildings}


def create(db: Session, data: BuildingIn) -> CampusBuilding:
    code = data.code.upper()
    if db.scalar(select(CampusBuilding.id).where(func.upper(CampusBuilding.code) == code)):
        raise ConflictError(f"A building with code {code} already exists")
    building = CampusBuilding(
        code=code, name=data.name, latitude=data.latitude, longitude=data.longitude
    )
    db.add(building)
    db.commit()
    return building


def update(db: Session, code: str, changes: BuildingUpdate) -> CampusBuilding:
    building = _get(db, code)
    if building.code.upper() in catalog_codes():
        raise ConflictError(FIXTURE_HINT)
    for field, value in changes.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(building, field, value)
    db.commit()
    return building


def delete(db: Session, code: str) -> None:
    """Delete a building. Events, classes and groups that used it keep working,
    without a building (the database sets their building to empty)."""
    building = _get(db, code)
    if building.code.upper() in catalog_codes():
        raise ConflictError(FIXTURE_HINT)
    db.delete(building)
    db.commit()


def _get(db: Session, code: str) -> CampusBuilding:
    building = db.scalar(
        select(CampusBuilding).where(func.upper(CampusBuilding.code) == code.upper())
    )
    if building is None:
        raise NotFoundError("Building not found")
    return building
