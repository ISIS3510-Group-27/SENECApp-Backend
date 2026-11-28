from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalyticsEvent, Attendance, CampusBuilding, MembershipRole, ScheduleBlock
from app.services.schedule import current_or_next_free_block, free_blocks_for_day
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_event, make_user

ADMIN_EMAIL = "organizer@uniandes.edu.co"
ADMIN = auth_header(ADMIN_EMAIL)
STUDENT = auth_header("attendee@uniandes.edu.co")


def _building(db: Session, code: str) -> CampusBuilding:
    return db.scalars(select(CampusBuilding).where(CampusBuilding.code == code)).one()


@pytest.fixture
def tennis_admin(db_session: Session) -> int:
    group = group_by_name(db_session, "Tennis Uniandes")
    add_member(db_session, make_user(db_session, ADMIN_EMAIL), group, MembershipRole.ADMIN)
    db_session.commit()
    return group.id


def test_admin_creates_event_and_members_list_it(
    client: TestClient, db_session: Session, tennis_admin: int
) -> None:
    starts = datetime.now(UTC) + timedelta(days=1)
    payload = {
        "title": "Doubles night",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=2)).isoformat(),
        "building_id": _building(db_session, "CD").id,
    }

    created = client.post(f"/api/v1/groups/{tennis_admin}/events", headers=ADMIN, json=payload)
    forbidden = client.post(f"/api/v1/groups/{tennis_admin}/events", headers=STUDENT, json=payload)
    listed = client.get("/api/v1/events", headers=ADMIN, params={"mine": True}).json()

    assert created.status_code == 201
    assert created.json()["group"]["name"] == "Tennis Uniandes"
    assert forbidden.status_code == 403
    assert [e["title"] for e in listed["items"]] == ["Doubles night"]


def test_event_times_must_be_ordered(client: TestClient, tennis_admin: int) -> None:
    starts = datetime.now(UTC) + timedelta(days=1)
    payload = {
        "title": "Backwards",
        "starts_at": starts.isoformat(),
        "ends_at": (starts - timedelta(hours=1)).isoformat(),
    }

    response = client.post(f"/api/v1/groups/{tennis_admin}/events", headers=ADMIN, json=payload)

    assert response.status_code == 422


def test_qr_check_in_flow(client: TestClient, db_session: Session, tennis_admin: int) -> None:
    building = _building(db_session, "CD")
    event = make_event(
        db_session,
        group_by_name(db_session, "Tennis Uniandes"),
        starts_in=timedelta(minutes=10),
        building_id=building.id,
    )
    db_session.commit()

    code = client.get(f"/api/v1/events/{event.id}/check-in-code", headers=ADMIN).json()
    student_code = client.get(f"/api/v1/events/{event.id}/check-in-code", headers=STUDENT)
    wrong = client.post(f"/api/v1/events/{event.id}/check-in", headers=STUDENT, json={"code": "x"})
    near = {"code": code["code"], "latitude": building.latitude, "longitude": building.longitude}
    first = client.post(f"/api/v1/events/{event.id}/check-in", headers=STUDENT, json=near)
    again = client.post(f"/api/v1/events/{event.id}/check-in", headers=STUDENT, json=near)

    assert code["qr_payload"].startswith("senecapp://check-in?event_id=")
    assert student_code.status_code == 403
    assert wrong.status_code == 422
    assert first.json()["already_checked_in"] is False
    assert first.json()["distance_m"] == 0
    assert again.json()["already_checked_in"] is True
    assert len(db_session.scalars(select(Attendance)).all()) == 1
    tracked = db_session.scalars(
        select(AnalyticsEvent).where(AnalyticsEvent.name == "event_checked_in")
    ).all()
    assert len(tracked) == 1


def test_check_in_rejects_far_away_or_out_of_window(
    client: TestClient, db_session: Session
) -> None:
    building = _building(db_session, "ML")
    group = group_by_name(db_session, "Coro Uniandes")
    soon = make_event(db_session, group, starts_in=timedelta(minutes=5), building_id=building.id)
    later = make_event(db_session, group, starts_in=timedelta(days=3), building_id=building.id)
    db_session.commit()

    far = client.post(
        f"/api/v1/events/{soon.id}/check-in",
        headers=STUDENT,
        json={"code": soon.check_in_code, "latitude": 4.65, "longitude": -74.05},
    )
    too_early = client.post(
        f"/api/v1/events/{later.id}/check-in", headers=STUDENT, json={"code": later.check_in_code}
    )

    assert far.status_code == 409
    assert too_early.status_code == 409


def test_replace_schedule_and_reject_overlaps(client: TestClient, db_session: Session) -> None:
    ml = _building(db_session, "ML").id
    blocks = [
        {"weekday": 0, "start_time": "08:30", "end_time": "10:00", "building_id": ml},
        {"weekday": 0, "start_time": "11:30", "end_time": "13:00", "title": "Cálculo"},
    ]

    saved = client.put("/api/v1/me/schedule", headers=STUDENT, json={"blocks": blocks})
    overlapping = client.put(
        "/api/v1/me/schedule",
        headers=STUDENT,
        json={"blocks": [*blocks, {"weekday": 0, "start_time": "09:00", "end_time": "09:30"}]},
    )

    assert saved.status_code == 200
    assert [b["start_time"] for b in saved.json()] == ["08:30:00", "11:30:00"]
    assert saved.json()[0]["building"]["code"] == "ML"
    assert overlapping.status_code == 422


def test_free_blocks_between_classes() -> None:
    monday = date(2026, 10, 5)
    blocks = [
        ScheduleBlock(weekday=0, start_time=time(7, 0), end_time=time(8, 20), building_id=1),
        ScheduleBlock(weekday=0, start_time=time(8, 30), end_time=time(10, 0), building_id=2),
        ScheduleBlock(weekday=0, start_time=time(13, 0), end_time=time(14, 30), building_id=3),
    ]

    free = free_blocks_for_day(blocks, monday)

    assert [(f.starts_at.time(), f.ends_at.time()) for f in free] == [
        (time(10, 0), time(13, 0)),
        (time(14, 30), time(21, 0)),
    ]
    assert free[0].previous_building_id == 2
    assert free[0].next_building_id == 3
    moment = free[0].starts_at + timedelta(minutes=45)
    current = current_or_next_free_block(blocks, moment)
    assert current is not None
    assert current.starts_at == moment
    assert current.minutes == 135


def test_invalid_null_updates_and_unicode_code(client, db_session, tennis_admin):
    event = make_event(db_session, group_by_name(db_session, "Tennis Uniandes"))
    db_session.commit()
    for field in ("title", "is_cancelled"):
        response = client.patch(f"/api/v1/events/{event.id}", headers=ADMIN, json={field: None})
        assert response.status_code == 422
    for field in ("full_name", "location_opt_in", "notifications_opt_in"):
        assert client.patch("/api/v1/me", headers=ADMIN, json={field: None}).status_code == 422
    response = client.post(
        f"/api/v1/events/{event.id}/check-in", headers=STUDENT, json={"code": "código inválido"}
    )
    assert response.status_code == 422


def test_inactive_group_events_are_hidden(client, db_session):
    group = group_by_name(db_session, "Tennis Uniandes")
    event = make_event(db_session, group)
    group.is_active = False
    db_session.commit()
    response = client.get("/api/v1/events", headers=STUDENT)
    assert response.status_code == 200
    assert event.id not in [item["id"] for item in response.json()["items"]]


def test_concurrent_check_in_is_idempotent(db_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.schemas.event import CheckInRequest
    from app.services.events import check_in

    with Session(db_engine) as setup:
        user = make_user(setup, "concurrent-checkin@uniandes.edu.co")
        event = make_event(
            setup, group_by_name(setup, "Tennis Uniandes"), starts_in=timedelta(minutes=1)
        )
        setup.commit()
        user_id, event_id, code = user.id, event.id, event.check_in_code
    barrier = Barrier(2)

    def attend(_):
        from app.models import User

        with Session(db_engine) as session:
            student = session.get(User, user_id)
            barrier.wait(timeout=10)
            return check_in(session, student, event_id, CheckInRequest(code=code), None)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attend, range(2)))
        assert sorted(result.already_checked_in for result in results) == [False, True]
        with Session(db_engine) as session:
            assert (
                len(
                    session.scalars(select(Attendance).where(Attendance.event_id == event_id)).all()
                )
                == 1
            )
    finally:
        from app.models import Event, User

        with Session(db_engine) as session:
            session.delete(session.get(Event, event_id))
            session.delete(session.get(User, user_id))
            session.commit()
