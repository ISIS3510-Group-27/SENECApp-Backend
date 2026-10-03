import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    CampusBuilding,
    CheckInMethod,
    Event,
    MembershipRole,
    RecommendationKind,
    RecommendationLog,
    ScheduleBlock,
    StudentGroup,
    User,
)
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_user

LEADER_EMAIL = "leader@uniandes.edu.co"
LEADER = auth_header(LEADER_EMAIL)
MEMBER_EMAIL = "member.one@uniandes.edu.co"
BOGOTA = ZoneInfo("America/Bogota")


@pytest.fixture
def group(db_session: Session) -> StudentGroup:
    group = group_by_name(db_session, "Tennis Uniandes")
    add_member(db_session, make_user(db_session, LEADER_EMAIL), group, MembershipRole.ADMIN)
    for email in (MEMBER_EMAIL, "member.two@uniandes.edu.co"):
        member = make_user(db_session, email)
        add_member(db_session, member, group)
        db_session.add(
            ScheduleBlock(user_id=member.id, weekday=0, start_time=time(7, 0), end_time=time(13, 0))
        )
    db_session.commit()
    return group


def _members(db: Session, group: StudentGroup) -> list[User]:
    return [m.user for m in group.memberships if m.role == MembershipRole.MEMBER]


def _past_event(db: Session, group: StudentGroup, weekday: int, hour: int, weeks_ago: int) -> Event:
    today = datetime.now(BOGOTA).date()
    day: date = today - timedelta(days=(today.weekday() - weekday) % 7 + 7 * weeks_ago)
    starts = datetime.combine(day, time(hour), tzinfo=BOGOTA)
    event = Event(
        group_id=group.id,
        title="Past session",
        starts_at=starts,
        ends_at=starts + timedelta(hours=2),
    )
    db.add(event)
    db.flush()
    return event


def test_best_times_skip_hours_when_members_are_in_class(
    client: TestClient, group: StudentGroup
) -> None:
    response = client.get(f"/api/v1/groups/{group.id}/insights/best-times", headers=LEADER)

    body = response.json()
    assert response.status_code == 200
    assert body["members_with_schedule"] == 2
    assert len(body["slots"]) == 3
    first = body["slots"][0]
    assert (first["weekday"], first["start_time"], first["end_time"]) == (0, "13:00", "15:00")
    assert first["free_members"] == 2
    assert all(not (s["weekday"] == 0 and s["start_time"] < "13:00") for s in body["slots"])
    assert datetime.fromisoformat(first["next_starts_at"]) > datetime.now(UTC)


def test_best_times_learn_from_past_attendance(
    client: TestClient, db_session: Session, group: StudentGroup
) -> None:
    members = _members(db_session, group)
    for weeks_ago in (1, 2):
        packed = _past_event(db_session, group, weekday=3, hour=18, weeks_ago=weeks_ago)
        _past_event(db_session, group, weekday=0, hour=13, weeks_ago=weeks_ago)
        for member in members:
            db_session.add(
                Attendance(event_id=packed.id, user_id=member.id, method=CheckInMethod.QR)
            )
    db_session.commit()

    body = client.get(f"/api/v1/groups/{group.id}/insights/best-times", headers=LEADER).json()

    first = body["slots"][0]
    assert body["past_events"] == 4
    assert (first["weekday"], first["start_time"]) == (3, "18:00")
    assert first["attendance_rate"] > body["slots"][1]["attendance_rate"]


def test_insights_are_for_group_admins_only(client: TestClient, group: StudentGroup) -> None:
    member = auth_header(MEMBER_EMAIL)

    best = client.get(f"/api/v1/groups/{group.id}/insights/best-times", headers=member)
    audience = client.get(f"/api/v1/groups/{group.id}/insights/audience", headers=member)

    assert best.status_code == 403
    assert audience.status_code == 403


def test_audience_answers_bq3_with_buildings(
    client: TestClient, db_session: Session, group: StudentGroup
) -> None:
    buildings = list(db_session.scalars(select(CampusBuilding).order_by(CampusBuilding.id)))
    busy, quiet = buildings[0], buildings[1]
    event = _past_event(db_session, group, weekday=2, hour=12, weeks_ago=0)
    for i in range(20):
        for building, hour, attends in ((busy, 12, i < 12), (quiet, 9, i < 2)):
            student = make_user(db_session, f"viewer{hour}.{i}@uniandes.edu.co")
            db_session.add(
                RecommendationLog(
                    request_id=uuid.uuid4(),
                    user_id=student.id,
                    kind=RecommendationKind.EVENT,
                    item_id=event.id,
                    rank=1,
                    score=0.5,
                    features={},
                    context={"weekday": 2, "hour": hour, "building_code": building.code},
                    model_version="test",
                )
            )
            if attends:
                db_session.add(
                    Attendance(event_id=event.id, user_id=student.id, method=CheckInMethod.QR)
                )
    db_session.commit()

    response = client.get(f"/api/v1/groups/{group.id}/insights/audience", headers=LEADER)

    body = response.json()
    assert response.status_code == 200
    assert "free block" in body["question"]
    best = body["best_time_and_place"][0]
    assert best["hour"] == 12
    assert best["building"]["code"] == busy.code
    assert best["interaction_rate"] == pytest.approx(0.6)
    assert body["by_building"][0]["building"]["name"] == busy.name
