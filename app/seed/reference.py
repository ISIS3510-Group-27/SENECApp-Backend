"""Reference (catalog) data: categories, interests, campus buildings and groups.

Every table is upserted by its natural key, so running this repeatedly is safe:
nothing is duplicated, and edits to the fixtures are applied to existing rows.
"""

import logging
from typing import Any

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.text import slugify
from app.db.base import Base
from app.models import CampusBuilding, Category, GroupInterest, Interest, StudentGroup
from app.seed.loader import load_reference_fixtures

logger = logging.getLogger(__name__)


def seed_reference(session: Session) -> None:
    fixtures = load_reference_fixtures()

    category_ids = _upsert(
        session,
        Category,
        "slug",
        [c.model_dump() for c in fixtures.categories],
    )
    interest_ids = _upsert(
        session,
        Interest,
        "name",
        [
            {"name": i.name, "slug": slugify(i.name), "category_id": category_ids[i.category]}
            for i in fixtures.interests
        ],
    )
    building_ids = _upsert(
        session,
        CampusBuilding,
        "code",
        [b.model_dump() for b in fixtures.campus_buildings],
    )
    group_ids = _upsert(
        session,
        StudentGroup,
        "name",
        [
            {
                "name": g.name,
                "category_id": category_ids[g.category],
                "description": g.description,
                "color": g.color,
                "image_url": _str_or_none(g.image_url),
                "verified": g.verified,
                "is_active": g.is_active,
                "founded_year": g.founded_year,
                "contact_email": g.contact_email,
                "instagram_url": _str_or_none(g.instagram_url),
                "website_url": _str_or_none(g.website_url),
                "meeting_building_id": building_ids.get(g.meeting_building),
            }
            for g in fixtures.student_groups
        ],
    )

    # Group tags are fully replaced for seeded groups so the DB mirrors the fixture.
    session.execute(delete(GroupInterest).where(GroupInterest.group_id.in_(group_ids.values())))
    session.execute(
        insert(GroupInterest),
        [
            {"group_id": group_ids[g.name], "interest_id": interest_ids[tag]}
            for g in fixtures.student_groups
            for tag in g.tags
        ],
    )

    logger.info(
        "Reference data seeded: %d categories, %d interests, %d buildings, %d groups",
        len(category_ids),
        len(interest_ids),
        len(building_ids),
        len(group_ids),
    )


def _upsert(
    session: Session, model: type[Base], key: str, rows: list[dict[str, Any]]
) -> dict[str, int]:
    """Insert ``rows`` or update existing ones matched by ``key``; return ``{key: id}``.

    Rows whose values did not change are left untouched (no ``updated_at`` bump).
    Insertion follows list order, so on an empty table IDs match fixture order.
    """
    table = model.__table__
    stmt = pg_insert(table).values(rows)
    columns = [name for name in rows[0] if name != key]
    changes = {name: stmt.excluded[name] for name in columns}
    if "updated_at" in table.c:
        changes["updated_at"] = func.now()

    stmt = stmt.on_conflict_do_update(
        index_elements=[key],
        set_=changes,
        where=or_(*(table.c[name].is_distinct_from(stmt.excluded[name]) for name in columns)),
    )
    session.execute(stmt)

    keys = [row[key] for row in rows]
    return dict(
        session.execute(select(table.c[key], table.c.id).where(table.c[key].in_(keys))).all()
    )


def _str_or_none(value: object | None) -> str | None:
    return str(value) if value is not None else None
