"""Members requesting admin access, decided by the group's own admins."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MembershipRole, Notification, NotificationType, StudentGroup, User
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_user

LEADER_EMAIL = "leader@uniandes.edu.co"
MEMBER_EMAIL = "member@uniandes.edu.co"
LEADER = auth_header(LEADER_EMAIL)
MEMBER = auth_header(MEMBER_EMAIL)
OUTSIDER = auth_header("outsider@uniandes.edu.co")


@pytest.fixture
def group(db_session: Session) -> StudentGroup:
    group = group_by_name(db_session, "Teatro Los Andes")
    add_member(db_session, make_user(db_session, LEADER_EMAIL), group, MembershipRole.ADMIN)
    add_member(db_session, make_user(db_session, MEMBER_EMAIL), group)
    db_session.commit()
    return group


def _notifications(db: Session, email: str) -> list[Notification]:
    return db.scalars(
        select(Notification)
        .join(User, User.id == Notification.user_id)
        .where(Notification.type == NotificationType.ADMIN_REQUEST, User.email == email)
    ).all()


def test_member_requests_and_admin_approves(
    client: TestClient, db_session: Session, group: StudentGroup
) -> None:
    url = f"/api/v1/groups/{group.id}/admin-requests"

    created = client.post(url, headers=MEMBER, json={"note": "I can run rehearsals"})
    again = client.post(url, headers=MEMBER, json={})
    member_view = client.get(f"/api/v1/groups/{group.id}", headers=MEMBER).json()
    leader_view = client.get(f"/api/v1/groups/{group.id}", headers=LEADER).json()
    pending = client.get(url, headers=LEADER).json()
    approved = client.post(f"{url}/{created.json()['id']}/approve", headers=LEADER)
    after = client.get(f"/api/v1/groups/{group.id}", headers=MEMBER).json()

    assert created.status_code == 201
    assert created.json()["requester"]["email"] == MEMBER_EMAIL
    assert again.status_code == 409  # one open request at a time
    assert member_view["admin_request_status"] == "pending"
    assert leader_view["pending_admin_requests"] == 1
    assert [r["note"] for r in pending] == ["I can run rehearsals"]
    assert approved.json()["status"] == "approved"
    assert after["my_role"] == "admin"
    assert after["admin_request_status"] is None
    assert len(_notifications(db_session, LEADER_EMAIL)) == 1  # told about the request
    assert len(_notifications(db_session, MEMBER_EMAIL)) == 1  # told about the decision


def test_rejected_member_can_ask_again(
    client: TestClient, db_session: Session, group: StudentGroup
) -> None:
    url = f"/api/v1/groups/{group.id}/admin-requests"
    first = client.post(url, headers=MEMBER).json()

    rejected = client.post(f"{url}/{first['id']}/reject", headers=LEADER)
    decided_twice = client.post(f"{url}/{first['id']}/approve", headers=LEADER)
    second = client.post(url, headers=MEMBER)

    assert rejected.json()["status"] == "rejected"
    assert decided_twice.status_code == 409
    assert second.status_code == 201
    view = client.get(f"/api/v1/groups/{group.id}", headers=MEMBER).json()
    assert view["my_role"] == "member"


def test_only_members_request_and_only_admins_decide(
    client: TestClient, group: StudentGroup
) -> None:
    url = f"/api/v1/groups/{group.id}/admin-requests"
    request = client.post(url, headers=MEMBER).json()

    assert client.post(url, headers=OUTSIDER).status_code == 403  # not a member
    assert client.post(url, headers=LEADER).status_code == 409  # already admin
    assert client.get(url, headers=MEMBER).status_code == 403
    assert client.post(f"{url}/{request['id']}/approve", headers=MEMBER).status_code == 403
    assert client.post(f"{url}/99999/approve", headers=LEADER).status_code == 404


def test_cannot_approve_someone_who_left(
    client: TestClient, db_session: Session, group: StudentGroup
) -> None:
    url = f"/api/v1/groups/{group.id}/admin-requests"
    request = client.post(url, headers=MEMBER).json()
    client.delete(f"/api/v1/groups/{group.id}/membership", headers=MEMBER)

    response = client.post(f"{url}/{request['id']}/approve", headers=LEADER)

    assert response.status_code == 409


def test_creator_can_publish_events_once_approved(client: TestClient) -> None:
    creator = auth_header("founder@uniandes.edu.co")
    created = client.post(
        "/api/v1/groups",
        headers=creator,
        json={
            "name": "Club de Escalada",
            "category": "sports",
            "description": "Bouldering sessions every week at the campus climbing wall.",
        },
    ).json()
    starts = datetime.now(UTC) + timedelta(days=1)
    event = {
        "title": "First session",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=1)).isoformat(),
    }

    while_pending = client.post(
        f"/api/v1/groups/{created['id']}/events", headers=creator, json=event
    )
    client.post(
        f"/api/v1/admin/groups/{created['id']}/approve",
        headers=auth_header("admin@uniandes.edu.co"),
    )
    approved = client.post(f"/api/v1/groups/{created['id']}/events", headers=creator, json=event)

    assert created["my_role"] == "admin"
    assert created["founded_year"] == datetime.now(UTC).year
    assert while_pending.status_code == 409
    assert approved.status_code == 201  # events themselves need no approval
