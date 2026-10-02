import json
from pathlib import Path

from app.seed.schemas import ReferenceFixtures

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_reference_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> ReferenceFixtures:
    """Read and validate every reference fixture file."""

    def read(name: str) -> list[dict]:
        return json.loads((fixtures_dir / f"{name}.json").read_text(encoding="utf-8"))

    return ReferenceFixtures(
        categories=read("categories"),
        interests=read("interests"),
        campus_buildings=read("campus_buildings"),
        student_groups=read("student_groups"),
    )
