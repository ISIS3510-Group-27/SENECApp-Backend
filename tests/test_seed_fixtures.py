import pytest
from pydantic import ValidationError

from app.core.text import slugify
from app.seed.loader import load_reference_fixtures
from app.seed.schemas import ReferenceFixtures

# Must stay first and in this order so seeded IDs 1..8 match the mobile prototypes.
PROTOTYPE_GROUPS = [
    "Tennis Uniandes",
    "Emprendedores Uniandes",
    "Viajeros Uniandes",
    "Motor's Uniandes",
    "AI & Machine Learning",
    "Teatro Los Andes",
    "Finance Society",
    "Fotografía Uniandes",
]


def test_reference_fixtures_are_valid() -> None:
    fixtures = load_reference_fixtures()

    assert fixtures.categories
    assert fixtures.interests
    assert fixtures.campus_buildings
    assert fixtures.student_groups


def test_prototype_groups_come_first_in_order() -> None:
    names = [g.name for g in load_reference_fixtures().student_groups]

    assert names[: len(PROTOTYPE_GROUPS)] == PROTOTYPE_GROUPS


def test_interest_slugs_are_unique() -> None:
    slugs = [slugify(i.name) for i in load_reference_fixtures().interests]

    assert len(slugs) == len(set(slugs))


def test_unknown_group_tag_is_rejected() -> None:
    fixtures = load_reference_fixtures().model_dump()
    fixtures["student_groups"][0]["tags"] = ["Not An Interest"]

    with pytest.raises(ValidationError, match="unknown tags"):
        ReferenceFixtures.model_validate(fixtures)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AI/ML", "ai-ml"),
        ("Fotografía Uniandes", "fotografia-uniandes"),
        ("Coffee & Conversation", "coffee-conversation"),
    ],
)
def test_slugify(value: str, expected: str) -> None:
    assert slugify(value) == expected
