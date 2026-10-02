import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalyticsEvent, User
from app.services.analytics import track
from tests.conftest import auth_header


def _event(**overrides: object) -> dict[str, object]:
    event = {
        "event_id": str(uuid.uuid4()),
        "name": "screen_view",
        "occurred_at": datetime.now(UTC).isoformat(),
        "session_id": "s-1",
        "screen": "explore",
        "app": "flutter",
        "app_version": "1.0.0",
        "platform": "android",
        "device_model": "Pixel 7",
        "os_version": "14",
        "properties": {"load_time_ms": 420},
    }
    return {**event, **overrides}


def test_batch_is_stored_with_user(client: TestClient, db_session: Session) -> None:
    events = [_event(), _event(name="app_error", properties={"fatal": False})]

    response = client.post(
        "/api/v1/analytics/events",
        json={"events": events},
        headers=auth_header("ana@uniandes.edu.co"),
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "duplicates": 0}
    stored = db_session.scalars(select(AnalyticsEvent).order_by(AnalyticsEvent.id)).all()
    assert [e.name for e in stored] == ["screen_view", "app_error"]
    assert stored[0].properties == {"load_time_ms": 420}
    assert stored[0].user_id is not None
    assert stored[0].source == "client"


def test_resent_batch_is_deduplicated(client: TestClient) -> None:
    events = [_event(), _event()]
    client.post("/api/v1/analytics/events", json={"events": events})

    response = client.post("/api/v1/analytics/events", json={"events": events})

    assert response.json() == {"accepted": 0, "duplicates": 2}


def test_anonymous_events_are_accepted(client: TestClient, db_session: Session) -> None:
    response = client.post("/api/v1/analytics/events", json={"events": [_event()]})

    assert response.status_code == 202
    assert db_session.scalars(select(AnalyticsEvent)).one().user_id is None


def test_future_timestamps_are_clamped(client: TestClient, db_session: Session) -> None:
    future = datetime.now(UTC) + timedelta(days=3)

    client.post(
        "/api/v1/analytics/events", json={"events": [_event(occurred_at=future.isoformat())]}
    )

    stored = db_session.scalars(select(AnalyticsEvent)).one()
    assert stored.occurred_at < future - timedelta(days=2)


def test_invalid_events_are_rejected(client: TestClient) -> None:
    bad_name = client.post("/api/v1/analytics/events", json={"events": [_event(name="Bad Name")]})
    empty = client.post("/api/v1/analytics/events", json={"events": []})

    assert bad_name.status_code == 422
    assert empty.status_code == 422


def test_server_events_use_request_context(client: TestClient, db_session: Session) -> None:
    user = User(email="server@uniandes.edu.co", full_name="Server")
    db_session.add(user)
    db_session.flush()

    track(db_session, "group_saved", user_id=user.id, properties={"group_id": 1})
    db_session.commit()

    stored = db_session.scalars(select(AnalyticsEvent)).one()
    assert stored.source == "server"
    assert stored.properties == {"group_id": 1}
