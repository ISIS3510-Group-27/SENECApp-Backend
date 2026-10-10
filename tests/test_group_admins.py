"""Group admins by email (fixture + admin endpoints) and group deactivation/deletion."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GroupAdminInvite, Membership, MembershipRole, StudentGroup
from app.seed.loader import load_reference_fixtures
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_user

ADMIN = auth_header("admin@uniandes.edu.co")
STUDENT = auth_header("student@uniandes.edu.co")
ORGANIZER_EMAIL = "organizer@uniandes.edu.co"
ORGANIZER = auth_header(ORGANIZER_EMAIL)


def _event_payload() -> dict:
    starts = datetime.now(UTC) + timedelta(days=1)
    return {
        "title": "Kick-off meeting",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=1)).isoformat(),
    }


def _role(db: Session, email: str, group: StudentGroup) -> MembershipRole | None:
    db.expire_all()
    membership = db.scalar(
        select(Membership).where(
            Membership.group_id == group.id,
            Membership.user.has(email=email),
        )
    )
    return membership.role if membership else None


# --- Who can be an admin ------------------------------------------------------------------


@pytest.mark.parametrize("email", ["n.salazars@uniandes.edu.co", "c.castilla@uniandes.edu.co"])
def test_super_admins_are_always_platform_admins(client: TestClient, email: str) -> None:
    # The test settings only list admin@uniandes.edu.co in ADMIN_EMAILS.
    response = client.get("/api/v1/admin/groups", headers=auth_header(email))

    assert response.status_code == 200


def test_fixture_cannot_assign_admins() -> None:
    fixtures = load_reference_fixtures().model_dump()
    fixtures["student_groups"][0]["admins"] = ["captain@uniandes.edu.co"]

    with pytest.raises(ValueError):
        type(load_reference_fixtures())(**fixtures)


def test_invited_admin_becomes_admin_on_first_sign_in(
    client: TestClient, db_session: Session
) -> None:
    group = group_by_name(db_session, "Tennis Uniandes")

    invited = client.post(
        f"/api/v1/admin/groups/{group.id}/admins", headers=ADMIN, json={"email": ORGANIZER_EMAIL}
    )
    detail = client.get(f"/api/v1/groups/{group.id}", headers=ORGANIZER).json()
    created = client.post(
        f"/api/v1/groups/{group.id}/events", headers=ORGANIZER, json=_event_payload()
    )

    assert invited.json()["pending_emails"] == [ORGANIZER_EMAIL]
    assert detail["my_role"] == "admin"
    assert db_session.scalars(select(GroupAdminInvite)).all() == []
    assert created.status_code == 201


# --- Admin endpoints --------------------------------------------------------------------------


def test_admins_can_be_added_listed_and_removed(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Coro Uniandes")
    member = make_user(db_session, "singer@uniandes.edu.co")
    add_member(db_session, member, group)
    db_session.commit()
    url = f"/api/v1/admin/groups/{group.id}/admins"

    promoted = client.post(url, headers=ADMIN, json={"email": "singer@uniandes.edu.co"})
    invited = client.post(url, headers=ADMIN, json={"email": "Future@Uniandes.edu.co"})
    listed = client.get(url, headers=ADMIN).json()

    assert promoted.status_code == 200
    assert [a["email"] for a in listed["admins"]] == ["singer@uniandes.edu.co"]
    assert invited.json()["pending_emails"] == ["future@uniandes.edu.co"]
    assert _role(db_session, "singer@uniandes.edu.co", group) == MembershipRole.ADMIN

    assert client.delete(f"{url}/singer@uniandes.edu.co", headers=ADMIN).status_code == 204
    assert client.delete(f"{url}/future@uniandes.edu.co", headers=ADMIN).status_code == 204
    assert client.delete(f"{url}/nobody@uniandes.edu.co", headers=ADMIN).status_code == 404
    # Demoted, not removed from the group.
    assert _role(db_session, "singer@uniandes.edu.co", group) == MembershipRole.MEMBER
    assert client.get(url, headers=ADMIN).json() == {
        "group_id": group.id,
        "admins": [],
        "pending_emails": [],
    }


def test_new_admin_can_create_events(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Basket Andes")
    client.get("/api/v1/me", headers=ORGANIZER)  # account exists

    before = client.post(
        f"/api/v1/groups/{group.id}/events", headers=ORGANIZER, json=_event_payload()
    )
    client.post(
        f"/api/v1/admin/groups/{group.id}/admins", headers=ADMIN, json={"email": ORGANIZER_EMAIL}
    )
    after = client.post(
        f"/api/v1/groups/{group.id}/events", headers=ORGANIZER, json=_event_payload()
    )

    assert before.status_code == 403
    assert after.status_code == 201


def test_admin_endpoints_are_for_platform_admins(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Coro Uniandes")
    body = {"email": "x@uniandes.edu.co"}

    assert client.get("/api/v1/admin/groups", headers=STUDENT).status_code == 403
    assert (
        client.post(
            f"/api/v1/admin/groups/{group.id}/admins", headers=STUDENT, json=body
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/admin/groups/{group.id}", headers=STUDENT).status_code == 403


def test_admin_email_must_be_able_to_sign_in(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Coro Uniandes")

    response = client.post(
        f"/api/v1/admin/groups/{group.id}/admins", headers=ADMIN, json={"email": "x@gmail.com"}
    )

    assert response.status_code == 422


# --- Deactivate / delete --------------------------------------------------------------------


def test_deactivated_group_is_hidden_until_reactivated(
    client: TestClient, db_session: Session
) -> None:
    group = group_by_name(db_session, "Runners Club")
    url = f"/api/v1/admin/groups/{group.id}"

    def found() -> int:
        return client.get("/api/v1/groups", headers=STUDENT, params={"q": "Runners"}).json()[
            "total"
        ]

    deactivated = client.patch(url, headers=ADMIN, json={"is_active": False})
    hidden = found()
    client.patch(url, headers=ADMIN, json={"is_active": True})

    assert deactivated.json()["is_active"] is False
    assert hidden == 0
    assert found() == 1


def test_delete_refuses_catalog_groups_but_deletes_proposals(
    client: TestClient, db_session: Session
) -> None:
    catalog = group_by_name(db_session, "Runners Club")
    proposal = client.post(
        "/api/v1/groups",
        headers=STUDENT,
        json={
            "name": "Grupo de Prueba",
            "category": "social",
            "description": "A proposal that will be deleted by the platform admin.",
        },
    ).json()
    listed = {g["name"]: g for g in client.get("/api/v1/admin/groups", headers=ADMIN).json()}

    refused = client.delete(f"/api/v1/admin/groups/{catalog.id}", headers=ADMIN)
    deleted = client.delete(f"/api/v1/admin/groups/{proposal['id']}", headers=ADMIN)

    assert listed["Runners Club"]["from_catalog"] is True
    assert listed["Grupo de Prueba"]["from_catalog"] is False
    assert listed["Grupo de Prueba"]["admin_emails"] == []  # the creator is only a member
    assert refused.status_code == 409
    assert "student_groups.json" in refused.json()["detail"]
    assert deleted.status_code == 204
    db_session.expire_all()
    assert db_session.get(StudentGroup, proposal["id"]) is None
    assert client.delete(f"/api/v1/admin/groups/{proposal['id']}", headers=ADMIN).status_code == 404
