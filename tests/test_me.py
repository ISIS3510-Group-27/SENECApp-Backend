from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Interest, UserInterest
from tests.conftest import auth_header

STUDENT = auth_header("sofia.arango@uniandes.edu.co")


def _interest_ids(db: Session, *names: str) -> list[int]:
    rows = db.execute(select(Interest.name, Interest.id).where(Interest.name.in_(names))).all()
    by_name = dict(rows)
    return [by_name[name] for name in names]


def test_catalog_lists_categories_and_interests(client: TestClient) -> None:
    categories = client.get("/api/v1/categories").json()
    interests = client.get("/api/v1/interests").json()

    assert len(categories) == 8
    assert len(interests) == 34
    tennis = next(i for i in interests if i["name"] == "Tennis")
    assert tennis["category"]["slug"] == "sports"


def test_update_profile_changes_only_sent_fields(client: TestClient) -> None:
    client.get("/api/v1/me", headers=STUDENT)

    response = client.patch(
        "/api/v1/me",
        headers=STUDENT,
        json={"program": "Ingeniería de Sistemas", "semester": 6, "location_opt_in": True},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["program"] == "Ingeniería de Sistemas"
    assert body["semester"] == 6
    assert body["location_opt_in"] is True
    assert body["full_name"] == "sofia.arango"


def test_update_profile_rejects_invalid_payload(client: TestClient) -> None:
    assert client.patch("/api/v1/me", headers=STUDENT, json={"semester": 0}).status_code == 422
    assert client.patch("/api/v1/me", headers=STUDENT, json={"email": "x@y.z"}).status_code == 422


def test_replace_interests_keeps_original_opt_in_time(
    client: TestClient, db_session: Session
) -> None:
    tennis, ai, startups = _interest_ids(db_session, "Tennis", "AI/ML", "Startups")
    user_id = client.get("/api/v1/me", headers=STUDENT).json()["id"]
    client.put("/api/v1/me/interests", headers=STUDENT, json={"interest_ids": [tennis, ai]})

    opted_in_at = datetime(2026, 1, 15, tzinfo=UTC)
    kept = db_session.get(UserInterest, (user_id, ai))
    kept.created_at = opted_in_at
    db_session.commit()

    response = client.put(
        "/api/v1/me/interests", headers=STUDENT, json={"interest_ids": [ai, startups]}
    )

    assert response.status_code == 200
    assert {i["name"] for i in response.json()["interests"]} == {"AI/ML", "Startups"}
    db_session.expire_all()
    assert db_session.get(UserInterest, (user_id, ai)).created_at == opted_in_at
    assert db_session.get(UserInterest, (user_id, tennis)) is None


def test_replace_interests_rejects_unknown_ids(client: TestClient) -> None:
    response = client.put("/api/v1/me/interests", headers=STUDENT, json={"interest_ids": [99999]})

    assert response.status_code == 422
