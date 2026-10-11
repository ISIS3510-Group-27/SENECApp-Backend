from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Event
from tests.conftest import auth_header
from tests.factories import group_by_name, make_event

ADMIN = auth_header("admin@uniandes.edu.co")
STUDENT = auth_header("student@uniandes.edu.co")
NEW = {"code": "rgd", "name": "Edificio RGD", "latitude": 4.6031, "longitude": -74.0662}


def test_admin_adds_edits_and_deletes_a_building(client: TestClient, db_session: Session) -> None:
    created = client.post("/api/v1/admin/buildings", headers=ADMIN, json=NEW)
    duplicate = client.post("/api/v1/admin/buildings", headers=ADMIN, json=NEW)
    renamed = client.patch(
        "/api/v1/admin/buildings/RGD", headers=ADMIN, json={"name": "Edificio Richard"}
    )
    listed = {b["code"] for b in client.get("/api/v1/buildings").json()}

    assert created.status_code == 201
    assert created.json()["code"] == "RGD"
    assert duplicate.status_code == 409
    assert renamed.json()["name"] == "Edificio Richard"
    assert "RGD" in listed

    # An event there keeps existing, without a building, after the delete.
    event = make_event(
        db_session,
        group_by_name(db_session, "Coro Uniandes"),
        starts_in=timedelta(days=1),
        building_id=created.json()["id"],
    )
    db_session.commit()
    assert client.delete("/api/v1/admin/buildings/rgd", headers=ADMIN).status_code == 204
    db_session.expire_all()
    assert db_session.get(Event, event.id).building_id is None
    assert "RGD" not in {b["code"] for b in client.get("/api/v1/buildings").json()}


def test_fixture_buildings_are_edited_in_the_file(client: TestClient) -> None:
    patch = client.patch("/api/v1/admin/buildings/ML", headers=ADMIN, json={"name": "Otro nombre"})
    delete = client.delete("/api/v1/admin/buildings/ML", headers=ADMIN)

    assert patch.status_code == 409
    assert delete.status_code == 409
    assert "campus_buildings.json" in delete.json()["detail"]


def test_buildings_are_managed_by_platform_admins_only(client: TestClient) -> None:
    assert client.post("/api/v1/admin/buildings", headers=STUDENT, json=NEW).status_code == 403
    assert client.delete("/api/v1/admin/buildings/ML", headers=STUDENT).status_code == 403
    bad = {**NEW, "latitude": 123}
    assert client.post("/api/v1/admin/buildings", headers=ADMIN, json=bad).status_code == 422
