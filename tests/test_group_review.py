"""Group proposals: pending review, visibility, flexible create payload, admin review."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.questions import QUESTIONS, Params
from app.models import (
    AnalyticsEvent,
    CampusBuilding,
    Notification,
    NotificationType,
    ReviewStatus,
    StudentGroup,
    UserInterest,
)
from app.services import notifications as notification_service
from tests.conftest import auth_header
from tests.factories import group_by_name, interest_by_name, make_event, make_user

CREATOR_EMAIL = "founder@uniandes.edu.co"
CREATOR = auth_header(CREATOR_EMAIL)
STUDENT = auth_header("student@uniandes.edu.co")
ADMIN = auth_header("admin@uniandes.edu.co")

PROPOSAL = {
    "name": "Ciberseguridad Uniandes",
    "category": "technology",
    "description": "CTF practice, cybersecurity talks and responsible disclosure workshops.",
    "contact_email": "ciber@uniandes.edu.co",
}


def _propose(client: TestClient, **overrides: object) -> dict:
    response = client.post("/api/v1/groups", headers=CREATOR, json={**PROPOSAL, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def _events(db: Session, name: str) -> list[AnalyticsEvent]:
    return db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.name == name)).all()


def _notifications(db: Session, user_id: int, type_: NotificationType) -> list[Notification]:
    return db.scalars(
        select(Notification).where(Notification.user_id == user_id, Notification.type == type_)
    ).all()


def _building(db: Session, code: str) -> CampusBuilding:
    return db.scalars(select(CampusBuilding).where(CampusBuilding.code == code)).one()


# --- Creation --------------------------------------------------------------------------


def test_created_group_is_pending_and_creator_is_a_member(
    client: TestClient, db_session: Session
) -> None:
    created = _propose(client)

    assert created["review_status"] == "pending"
    assert created["rejection_reason"] is None
    assert created["my_role"] == "member"  # admins are assigned only by platform admins
    assert created["member_count"] == 1
    assert created["contact_email"] == "ciber@uniandes.edu.co"
    [event] = _events(db_session, "group_created")
    assert event.properties["group_id"] == created["id"]
    assert _events(db_session, "group_viewed") == []  # the creator's own view is not traffic


def test_category_can_be_given_by_slug_or_id(client: TestClient, db_session: Session) -> None:
    technology = interest_by_name(db_session, "Cybersecurity").category_id
    by_id = {k: v for k, v in PROPOSAL.items() if k != "category"}
    by_id |= {"category_id": technology, "name": "Grupo Por Id"}

    by_slug = client.post("/api/v1/groups", headers=CREATOR, json=PROPOSAL)
    with_id = client.post("/api/v1/groups", headers=CREATOR, json=by_id)

    assert by_slug.json()["category"]["slug"] == "technology"
    assert with_id.json()["category"]["id"] == technology


@pytest.mark.parametrize(
    "changes",
    [
        {"category": None},  # neither
        {"category_id": 1},  # both
        {"category": "underwater-basket-weaving"},  # unknown slug
        {"category": None, "category_id": 99999},  # unknown id
        {"contact_email": "not-an-email"},
        {"tag_ids": [99999]},
        {"tag_ids": list(range(1, 10))},  # more than 8
    ],
)
def test_invalid_proposals_are_rejected(client: TestClient, changes: dict) -> None:
    payload = {key: value for key, value in {**PROPOSAL, **changes}.items() if value is not None}

    assert client.post("/api/v1/groups", headers=CREATOR, json=payload).status_code == 422


def test_duplicate_name_is_a_conflict(client: TestClient) -> None:
    _propose(client)

    response = client.post("/api/v1/groups", headers=CREATOR, json=PROPOSAL)

    assert response.status_code == 409


def test_fallback_tags_come_from_name_and_description(client: TestClient) -> None:
    created = _propose(
        client,
        name="Club de ROBÓTICS y Data Science",
        description="We build robots, play Video Games and train AI/ML models together.",
    )

    # Case- and accent-insensitive; the first 3 in order of appearance (AI/ML is 4th).
    # Responses list tags alphabetically.
    assert [t["name"] for t in created["tags"]] == ["Data Science", "Robotics", "Video Games"]


def test_fallback_tag_is_the_most_popular_interest_when_nothing_matches(
    client: TestClient, db_session: Session
) -> None:
    motorsport = interest_by_name(db_session, "Motorsport")
    for i in range(2):
        fan = make_user(db_session, f"racer{i}@uniandes.edu.co")
        db_session.add(UserInterest(user_id=fan.id, interest_id=motorsport.id))
    db_session.commit()

    created = _propose(
        client,
        name="Garage Night",
        category="cars",
        description="Monthly meetups to share projects, tools and road trip stories.",
    )

    assert [t["name"] for t in created["tags"]] == ["Motorsport"]


def test_explicit_tags_are_kept(client: TestClient, db_session: Session) -> None:
    debate = interest_by_name(db_session, "Debate")

    created = _propose(client, tag_ids=[debate.id])

    assert [t["name"] for t in created["tags"]] == ["Debate"]


# --- Visibility while pending -------------------------------------------------------------


def test_pending_group_is_only_visible_to_its_creator(client: TestClient) -> None:
    created = _propose(client)
    url = f"/api/v1/groups/{created['id']}"

    assert client.get(url, headers=CREATOR).status_code == 200
    assert client.get(url, headers=STUDENT).status_code == 404
    assert client.put(f"{url}/save", headers=STUDENT).status_code == 404
    my_groups = client.get("/api/v1/me/groups", headers=CREATOR).json()
    assert [(g["name"], g["review_status"]) for g in my_groups] == [
        ("Ciberseguridad Uniandes", "pending")
    ]
    for headers in (CREATOR, STUDENT):
        search = client.get("/api/v1/groups", headers=headers, params={"q": "Ciberseguridad"})
        assert search.json()["total"] == 0


def test_pending_group_is_not_recommended(client: TestClient, db_session: Session) -> None:
    student = make_user(db_session, "student@uniandes.edu.co")
    db_session.add(
        UserInterest(
            user_id=student.id, interest_id=interest_by_name(db_session, "Cybersecurity").id
        )
    )
    db_session.commit()
    created = _propose(client)

    def recommended() -> set[str]:
        body = client.get("/api/v1/recommendations/groups", headers=STUDENT, params={"limit": 30})
        return {item["group"]["name"] for item in body.json()["items"]}

    before = recommended()
    client.post(f"/api/v1/admin/groups/{created['id']}/approve", headers=ADMIN)

    assert "Ciberseguridad Uniandes" not in before
    assert "Ciberseguridad Uniandes" in recommended()


def test_events_of_pending_groups_are_hidden(client: TestClient, db_session: Session) -> None:
    created = _propose(client)
    pending = db_session.get(StudentGroup, created["id"])
    building = _building(db_session, "ML")
    soon = timedelta(minutes=30)
    make_event(db_session, pending, starts_in=soon, building_id=building.id, title="Hidden CTF")
    make_event(
        db_session,
        group_by_name(db_session, "Open Source Uniandes"),
        starts_in=soon,
        building_id=building.id,
        title="Public hack night",
    )
    db_session.commit()

    listed = client.get("/api/v1/events", headers=STUDENT, params={"limit": 100}).json()
    free_now = client.get("/api/v1/recommendations/events/free-now", headers=STUDENT).json()

    listed_titles = {e["title"] for e in listed["items"]}
    suggested_titles = {item["event"]["title"] for item in free_now["items"]}
    assert "Public hack night" in listed_titles
    assert "Hidden CTF" not in listed_titles
    assert "Public hack night" in suggested_titles
    assert "Hidden CTF" not in suggested_titles


def test_cannot_join_or_publish_events_while_pending(client: TestClient) -> None:
    created = _propose(client)
    starts = datetime.now(UTC) + timedelta(days=1)

    join = client.post(f"/api/v1/groups/{created['id']}/join", headers=STUDENT, json={})
    # Not even an admin assigned by a platform admin can publish before approval.
    client.post(
        f"/api/v1/admin/groups/{created['id']}/admins", headers=ADMIN, json={"email": CREATOR_EMAIL}
    )
    event = client.post(
        f"/api/v1/groups/{created['id']}/events",
        headers=CREATOR,
        json={
            "title": "First meeting",
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(hours=1)).isoformat(),
        },
    )

    assert join.status_code == 409
    assert event.status_code == 409
    assert "pending review" in event.json()["detail"]


# --- Admin review ---------------------------------------------------------------------------


def test_pending_list_is_admin_only_and_oldest_first(
    client: TestClient, db_session: Session
) -> None:
    first = _propose(client)
    second = _propose(client, name="Otro Grupo Nuevo")
    db_session.get(StudentGroup, second["id"]).created_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()

    pending = client.get("/api/v1/admin/groups/pending", headers=ADMIN)

    assert client.get("/api/v1/admin/groups/pending", headers=STUDENT).status_code == 403
    assert [g["id"] for g in pending.json()] == [second["id"], first["id"]]
    assert pending.json()[0]["creator"]["email"] == CREATOR_EMAIL


def test_approve_publishes_the_group_and_notifies(client: TestClient, db_session: Session) -> None:
    created = _propose(client)
    creator_id = db_session.get(StudentGroup, created["id"]).created_by_id
    url = f"/api/v1/admin/groups/{created['id']}"

    forbidden = client.post(f"{url}/approve", headers=STUDENT)
    approved = client.post(f"{url}/approve", headers=ADMIN)
    again = client.post(f"{url}/approve", headers=ADMIN)
    reject_after = client.post(f"{url}/reject", headers=ADMIN, json={"reason": "Too late"})

    assert forbidden.status_code == 403
    assert approved.status_code == 200
    assert approved.json()["review_status"] == "approved"
    assert approved.json()["reviewed_at"] is not None
    assert again.status_code == 409
    assert reject_after.status_code == 409
    [review] = _notifications(db_session, creator_id, NotificationType.GROUP_REVIEW)
    assert review.data["review_status"] == "approved"
    assert len(_events(db_session, "group_approved")) == 1
    search = client.get("/api/v1/groups", headers=STUDENT, params={"q": "Ciberseguridad"})
    assert search.json()["total"] == 1
    joined = client.post(f"/api/v1/groups/{created['id']}/join", headers=STUDENT, json={})
    assert joined.status_code == 200


def test_reject_keeps_the_group_hidden_and_tells_the_creator(
    client: TestClient, db_session: Session
) -> None:
    fan = make_user(db_session, "fan@uniandes.edu.co")
    db_session.add(
        UserInterest(user_id=fan.id, interest_id=interest_by_name(db_session, "Cybersecurity").id)
    )
    db_session.commit()
    created = _propose(client)
    url = f"/api/v1/admin/groups/{created['id']}/reject"

    missing = client.post(url, headers=ADMIN, json={})
    blank = client.post(url, headers=ADMIN, json={"reason": "   "})
    too_long = client.post(url, headers=ADMIN, json={"reason": "x" * 501})
    rejected = client.post(url, headers=ADMIN, json={"reason": "Duplicates an existing RSO"})
    again = client.post(url, headers=ADMIN, json={"reason": "Again"})

    assert [missing.status_code, blank.status_code, too_long.status_code] == [422, 422, 422]
    assert rejected.json()["review_status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "Duplicates an existing RSO"
    assert again.status_code == 409
    group = db_session.get(StudentGroup, created["id"])
    [review] = _notifications(db_session, group.created_by_id, NotificationType.GROUP_REVIEW)
    assert "Duplicates an existing RSO" in review.body
    assert _notifications(db_session, fan.id, NotificationType.GROUP_RECOMMENDATION) == []
    assert len(_events(db_session, "group_rejected")) == 1
    assert client.get(f"/api/v1/groups/{created['id']}", headers=STUDENT).status_code == 404
    [mine] = client.get("/api/v1/me/groups", headers=CREATOR).json()
    assert (mine["review_status"], mine["rejection_reason"]) == (
        "rejected",
        "Duplicates an existing RSO",
    )


def test_review_notifications_do_not_change_bq8(db_session: Session) -> None:
    user = make_user(db_session, "reader@uniandes.edu.co")
    notification_service.notify(db_session, [user.id], NotificationType.NEW_EVENT, "t", "b")
    now = datetime.now(UTC)
    params = Params(since=now - timedelta(days=1), until=now + timedelta(minutes=1))

    before = QUESTIONS["8"].compute(db_session, params)
    notification_service.notify(db_session, [user.id], NotificationType.GROUP_REVIEW, "t", "b")
    after = QUESTIONS["8"].compute(db_session, params)

    assert after == before
    assert {row["type"] for row in after[0]["types"]} == {"new_event"}


def test_seeded_groups_are_approved(db_session: Session) -> None:
    statuses = set(db_session.scalars(select(StudentGroup.review_status)))

    assert statuses == {ReviewStatus.APPROVED}
