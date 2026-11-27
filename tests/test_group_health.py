from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Attendance, CheckInMethod, MembershipRole, StudentGroup, User
from app.services import reengagement
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_event, make_user

LEADER_EMAIL = "health.leader@uniandes.edu.co"
LEADER = auth_header(LEADER_EMAIL)
MEMBER_EMAIL = "health0@uniandes.edu.co"


def _weekly_events(db: Session, group: StudentGroup, attendees_per_week: list[list[User]]) -> None:
    weeks = len(attendees_per_week)
    for index, attendees in enumerate(attendees_per_week):
        event = make_event(
            db, group, starts_in=-(weeks - index) * timedelta(days=7) + timedelta(days=3)
        )
        for user in attendees:
            db.add(Attendance(event_id=event.id, user_id=user.id, method=CheckInMethod.QR))
    db.flush()


@pytest.fixture
def group(db_session: Session) -> tuple[StudentGroup, list[User]]:
    group = group_by_name(db_session, "Fotografía Uniandes")
    add_member(db_session, make_user(db_session, LEADER_EMAIL), group, MembershipRole.ADMIN)
    members = [make_user(db_session, f"health{i}@uniandes.edu.co") for i in range(6)]
    for member in members:
        add_member(db_session, member, group)
    db_session.commit()
    return group, members


def test_health_shows_eight_weeks_and_flags_a_decline(
    client: TestClient, db_session: Session, group: tuple[StudentGroup, list[User]]
) -> None:
    team, members = group
    _weekly_events(db_session, team, [members] * 4 + [members[:2]] * 4)
    db_session.commit()

    response = client.get(f"/api/v1/groups/{team.id}/insights/health", headers=LEADER)

    assert response.status_code == 200
    body = response.json()
    assert [week["attendance"] for week in body["weeks"]] == [6, 6, 6, 6, 2, 2, 2, 2]
    assert body["declining"] is True
    assert body["baseline"] == 6
    assert body["recent"] == 2
    assert body["decline_pct"] > 0.3
    assert body["question"].startswith("Which active student groups")


def test_health_is_calm_for_a_steady_group(
    client: TestClient, db_session: Session, group: tuple[StudentGroup, list[User]]
) -> None:
    team, members = group
    _weekly_events(db_session, team, [members] * 8)
    db_session.commit()

    body = client.get(f"/api/v1/groups/{team.id}/insights/health", headers=LEADER).json()

    assert body["declining"] is False
    assert body["case"] is None


def test_health_includes_the_open_reengagement_case(
    client: TestClient, db_session: Session, group: tuple[StudentGroup, list[User]]
) -> None:
    team, members = group
    _weekly_events(db_session, team, [members] * 4 + [members[:2]] * 4)
    db_session.commit()
    reengagement.detect_declines(db_session, datetime.now(UTC))

    body = client.get(f"/api/v1/groups/{team.id}/insights/health", headers=LEADER).json()

    assert body["case"] is not None
    assert body["case"]["lapsed_members"] == 4
    assert {arm["arm"] for arm in body["arms"]} <= {"event_reminders", "group_messages"}


def test_health_is_for_group_admins_only(
    client: TestClient, group: tuple[StudentGroup, list[User]]
) -> None:
    team, _ = group
    response = client.get(
        f"/api/v1/groups/{team.id}/insights/health", headers=auth_header(MEMBER_EMAIL)
    )

    assert response.status_code == 403
