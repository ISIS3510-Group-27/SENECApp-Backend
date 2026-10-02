from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnalyticsEvent,
    DeviceToken,
    MembershipRole,
    Notification,
    NotificationType,
    PushStatus,
    UserInterest,
)
from app.services import notifications as notification_service
from app.services.push import PushMessage, PushResult
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, interest_by_name, make_user


class FakePushSender:
    enabled = True

    def __init__(self, invalid: set[str] | None = None) -> None:
        self.sent: list[PushMessage] = []
        self.invalid = invalid or set()

    def send(self, messages: list[PushMessage]) -> PushResult:
        self.sent.extend(messages)
        tokens = {m.token for m in messages}
        return PushResult(delivered_tokens=tokens - self.invalid, invalid_tokens=self.invalid)


@pytest.fixture
def push(monkeypatch: pytest.MonkeyPatch) -> FakePushSender:
    sender = FakePushSender(invalid={"stale-token-0000"})
    monkeypatch.setattr(notification_service, "get_push_sender", lambda: sender)
    return sender


@pytest.fixture
def chat(db_session: Session) -> dict[str, object]:
    """Teatro Los Andes with an admin, an opted-in member and an opted-out member."""
    group = group_by_name(db_session, "Teatro Los Andes")
    admin = make_user(db_session, "director@uniandes.edu.co")
    member = make_user(db_session, "actor@uniandes.edu.co")
    quiet = make_user(db_session, "quiet@uniandes.edu.co", notifications_opt_in=False)
    add_member(db_session, admin, group, MembershipRole.ADMIN)
    add_member(db_session, member, group)
    add_member(db_session, quiet, group)
    db_session.commit()
    return {"group": group, "admin": admin, "member": member, "quiet": quiet}


def _notifications(db: Session, user_id: int) -> list[Notification]:
    return db.scalars(select(Notification).where(Notification.user_id == user_id)).all()


def test_chat_message_notifies_other_opted_in_members(
    client: TestClient, db_session: Session, chat: dict, push: FakePushSender
) -> None:
    group, member, quiet = chat["group"], chat["member"], chat["quiet"]
    client.post(
        "/api/v1/me/devices",
        headers=auth_header("actor@uniandes.edu.co"),
        json={"token": "actor-device-token", "platform": "android", "app": "kotlin"},
    )

    posted = client.post(
        f"/api/v1/groups/{group.id}/messages",
        headers=auth_header("director@uniandes.edu.co"),
        json={"body": "Rehearsal moved to 6 pm"},
    )
    history = client.get(
        f"/api/v1/groups/{group.id}/messages", headers=auth_header("actor@uniandes.edu.co")
    )

    assert posted.status_code == 201
    assert [m["body"] for m in history.json()] == ["Rehearsal moved to 6 pm"]
    [notification] = _notifications(db_session, member.id)
    assert notification.type == NotificationType.GROUP_MESSAGE
    assert notification.push_status == PushStatus.SENT
    assert _notifications(db_session, quiet.id) == []
    assert _notifications(db_session, chat["admin"].id) == []
    assert push.sent[0].data["notification_id"] == str(notification.id)


def test_non_members_cannot_read_or_post(client: TestClient, chat: dict) -> None:
    outsider = auth_header("outsider@uniandes.edu.co")
    url = f"/api/v1/groups/{chat['group'].id}/messages"

    assert client.get(url, headers=outsider).status_code == 403
    assert client.post(url, headers=outsider, json={"body": "hi"}).status_code == 403


def test_new_event_notifies_members(
    client: TestClient, db_session: Session, chat: dict, push: FakePushSender
) -> None:
    starts = datetime.now(UTC) + timedelta(days=2)

    client.post(
        f"/api/v1/groups/{chat['group'].id}/events",
        headers=auth_header("director@uniandes.edu.co"),
        json={
            "title": "Opening night",
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(hours=2)).isoformat(),
        },
    )

    [notification] = _notifications(db_session, chat["member"].id)
    assert notification.type == NotificationType.NEW_EVENT
    assert notification.body == "Opening night"
    assert notification.push_status == PushStatus.SKIPPED  # member has no devices


def test_new_group_notifies_students_with_matching_interests(
    client: TestClient, db_session: Session, push: FakePushSender
) -> None:
    climbing = interest_by_name(db_session, "Climbing")
    fan = make_user(db_session, "climber@uniandes.edu.co")
    db_session.add(UserInterest(user_id=fan.id, interest_id=climbing.id))
    db_session.commit()

    client.post(
        "/api/v1/groups",
        headers=auth_header("founder@uniandes.edu.co"),
        json={
            "name": "Escalada Uniandes",
            "category_id": climbing.category_id,
            "description": "Bouldering sessions and outdoor climbing trips every month.",
            "tag_ids": [climbing.id],
        },
    )

    [notification] = _notifications(db_session, fan.id)
    assert notification.type == NotificationType.GROUP_RECOMMENDATION
    assert "Escalada Uniandes" in notification.body


def test_open_and_dismiss_are_tracked_once(
    client: TestClient, db_session: Session, chat: dict
) -> None:
    member = chat["member"]
    notification_service.notify(
        db_session, [member.id], NotificationType.NEW_EVENT, "Title", "Body"
    )
    headers = auth_header("actor@uniandes.edu.co")
    inbox = client.get("/api/v1/me/notifications", headers=headers).json()
    notification_id = inbox["items"][0]["id"]

    client.post(f"/api/v1/me/notifications/{notification_id}/open", headers=headers)
    client.post(f"/api/v1/me/notifications/{notification_id}/open", headers=headers)
    client.post(f"/api/v1/me/notifications/{notification_id}/dismiss", headers=headers)
    after = client.get("/api/v1/me/notifications", headers=headers).json()
    someone_else = client.post(
        f"/api/v1/me/notifications/{notification_id}/open",
        headers=auth_header("director@uniandes.edu.co"),
    )

    assert inbox["unread_count"] == 1
    assert after["unread_count"] == 0
    assert after["items"][0]["opened_at"] is not None
    assert someone_else.status_code == 404
    names = db_session.scalars(
        select(AnalyticsEvent.name).where(AnalyticsEvent.name.like("notification_%"))
    ).all()
    assert sorted(names) == ["notification_dismissed", "notification_opened"]


def test_invalid_push_tokens_are_removed(
    db_session: Session, chat: dict, push: FakePushSender
) -> None:
    member = chat["member"]
    db_session.add(DeviceToken(user_id=member.id, token="stale-token-0000"))
    db_session.commit()

    [notification] = notification_service.notify(
        db_session, [member.id], NotificationType.NEW_EVENT, "Title", "Body"
    )

    assert notification.push_status == PushStatus.FAILED
    assert db_session.scalars(select(DeviceToken)).all() == []
