from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Attendance,
    CheckInMethod,
    GroupMessage,
    MessageKind,
    Notification,
    NotificationType,
    ReengagementArm,
    ReengagementCase,
    ReengagementStatus,
    StudentGroup,
    User,
)
from app.services import reengagement
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_event, make_user

NOW = datetime.now(UTC)


def _weekly_events(db: Session, group: StudentGroup, attendees_per_week: list[list[User]]) -> None:
    """One event per week, oldest first, ending one week before NOW."""
    weeks = len(attendees_per_week)
    for index, attendees in enumerate(attendees_per_week):
        event = make_event(
            db, group, starts_in=-(weeks - index) * timedelta(days=7) + timedelta(days=3)
        )
        for user in attendees:
            db.add(Attendance(event_id=event.id, user_id=user.id, method=CheckInMethod.QR))
    db.flush()


@pytest.fixture
def declining_group(db_session: Session) -> tuple[StudentGroup, list[User]]:
    group = group_by_name(db_session, "Fotografía Uniandes")
    members = [make_user(db_session, f"photo{i}@uniandes.edu.co") for i in range(8)]
    for member in members:
        add_member(db_session, member, group)
    regulars, occasional = members[:2], members[2:6]
    # 4 baseline weeks with 6 attendees, then 4 weeks with only the 2 regulars.
    _weekly_events(db_session, group, [regulars + occasional] * 4 + [regulars] * 4)
    db_session.commit()
    return group, members


def test_is_declining_requires_consecutive_drop_over_30_percent() -> None:
    assert reengagement.is_declining([6, 6, 6, 6, 2, 2, 2, 2])[0] is True
    assert reengagement.is_declining([6, 6, 6, 6, 5, 5, 5, 5])[0] is False  # only -17%
    assert reengagement.is_declining([6, 6, 6, 6, 1, 1, 1, 9])[0] is False  # not consecutive
    assert reengagement.is_declining([1, 1, 1, 1, 0, 0, 0, 0])[0] is False  # baseline too small


def test_detection_opens_one_case_with_lapsed_members(
    db_session: Session, declining_group: tuple[StudentGroup, list[User]]
) -> None:
    group, members = declining_group

    first = reengagement.detect_declines(db_session, NOW)
    second = reengagement.detect_declines(db_session, NOW)

    [case] = first
    assert second == []
    assert case.group_id == group.id
    assert case.weekly_attendance == [6, 6, 6, 6, 2, 2, 2, 2]
    assert case.decline_pct == pytest.approx(0.6667, abs=1e-3)
    assert sorted(case.lapsed_user_ids) == sorted(m.id for m in members[2:6])


def test_arms_are_balanced() -> None:
    from collections import Counter

    counts = Counter({ReengagementArm.EVENT_REMINDERS: 2, ReengagementArm.GROUP_MESSAGES: 1})

    assert reengagement._assign_arm(1, NOW, counts) == ReengagementArm.GROUP_MESSAGES


def test_reminder_arm_notifies_only_lapsed_members_once(
    db_session: Session, declining_group: tuple[StudentGroup, list[User]]
) -> None:
    group, members = declining_group
    [case] = reengagement.detect_declines(db_session, NOW)
    case.arm = ReengagementArm.EVENT_REMINDERS
    make_event(db_session, group, starts_in=timedelta(hours=5), title="Photowalk")
    db_session.commit()

    sent = reengagement.send_event_reminders(db_session, NOW)
    sent_again = reengagement.send_event_reminders(db_session, NOW)

    assert sent == 4
    assert sent_again == 0
    reminded = db_session.scalars(
        select(Notification.user_id).where(Notification.type == NotificationType.EVENT_REMINDER)
    ).all()
    assert sorted(reminded) == sorted(m.id for m in members[2:6])


def test_message_arm_posts_weekly_system_message(
    db_session: Session, declining_group: tuple[StudentGroup, list[User]]
) -> None:
    group, _ = declining_group
    [case] = reengagement.detect_declines(db_session, NOW)
    case.arm = ReengagementArm.GROUP_MESSAGES
    db_session.commit()

    assert reengagement.send_group_messages(db_session, NOW) == 1
    assert reengagement.send_group_messages(db_session, NOW + timedelta(days=1)) == 0
    assert reengagement.send_group_messages(db_session, NOW + timedelta(days=8)) == 1
    kinds = db_session.scalars(
        select(GroupMessage.kind).where(GroupMessage.group_id == group.id)
    ).all()
    assert kinds == [MessageKind.SYSTEM, MessageKind.SYSTEM]


def test_evaluation_records_return_rate(
    db_session: Session, declining_group: tuple[StudentGroup, list[User]]
) -> None:
    group, members = declining_group
    [case] = reengagement.detect_declines(db_session, NOW)
    comeback = make_event(db_session, group, starts_in=timedelta(days=3))
    for returning in members[2:4]:
        db_session.add(
            Attendance(event_id=comeback.id, user_id=returning.id, method=CheckInMethod.QR)
        )
    db_session.commit()

    assert reengagement.evaluate_cases(db_session, NOW + timedelta(days=10)) == 0
    assert reengagement.evaluate_cases(db_session, NOW + timedelta(days=29)) == 1

    db_session.refresh(case)
    assert case.status == ReengagementStatus.COMPLETED
    assert case.returned_count == 2
    assert case.return_rate == 0.5


def test_admin_endpoints_require_admin(client: TestClient) -> None:
    student = auth_header("student@uniandes.edu.co")
    admin = auth_header("admin@uniandes.edu.co")
    release = {
        "app": "flutter",
        "version": "1.2.0",
        "released_at": NOW.isoformat(),
        "feature_area": "recommendations",
    }

    assert client.get("/api/v1/admin/releases", headers=student).status_code == 403
    assert client.post("/api/v1/admin/releases", headers=admin, json=release).status_code == 201
    assert client.post("/api/v1/admin/releases", headers=admin, json=release).status_code == 409
    run = client.post("/api/v1/admin/jobs/detect_attendance_declines/run", headers=admin).json()
    jobs = client.get("/api/v1/admin/jobs", headers=admin).json()

    assert run["status"] == "succeeded"
    assert next(j for j in jobs if j["name"] == "detect_attendance_declines")["last_run"]
    assert client.post("/api/v1/admin/jobs/nope/run", headers=admin).status_code == 404


def test_cases_endpoint_reports_live_return_rate(
    client: TestClient, db_session: Session, declining_group: tuple[StudentGroup, list[User]]
) -> None:
    reengagement.detect_declines(db_session, NOW)

    [case] = client.get(
        "/api/v1/admin/reengagement/cases", headers=auth_header("admin@uniandes.edu.co")
    ).json()

    assert case["lapsed_members"] == 4
    assert case["returned_members"] == 0
    assert case["status"] == "active"
    assert db_session.scalars(select(ReengagementCase)).one().arm in set(ReengagementArm)
