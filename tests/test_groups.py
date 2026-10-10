from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalyticsEvent, Membership, MembershipRole, MembershipStatus
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, interest_by_name, make_event, make_user

STUDENT = auth_header("explorer@uniandes.edu.co")


def _events(db: Session, name: str) -> list[AnalyticsEvent]:
    return db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.name == name)).all()


def test_explore_lists_only_active_groups(client: TestClient) -> None:
    page = client.get("/api/v1/groups", headers=STUDENT, params={"limit": 100}).json()

    assert page["total"] == 23
    assert "Club de Ajedrez" not in {g["name"] for g in page["items"]}


def test_browsing_without_filters_is_not_logged_as_search(
    client: TestClient, db_session: Session
) -> None:
    client.get("/api/v1/groups", headers=STUDENT)

    assert _events(db_session, "group_searched") == []


def test_search_by_category_and_text(client: TestClient, db_session: Session) -> None:
    sports = client.get("/api/v1/groups", headers=STUDENT, params={"category": "sports"}).json()
    tennis = client.get(
        "/api/v1/groups", headers=STUDENT, params={"q": "tennis", "verified": True}
    ).json()

    assert {g["name"] for g in sports["items"]} == {
        "Tennis Uniandes",
        "Fútbol Uniandes",
        "Basket Andes",
        "Runners Club",
    }
    assert [g["name"] for g in tennis["items"]] == ["Tennis Uniandes"]
    searches = _events(db_session, "group_searched")
    assert [s.properties["filters"] for s in searches] == [["categories"], ["q", "verified"]]


def test_search_by_interest_and_upcoming_events(client: TestClient, db_session: Session) -> None:
    hiking = interest_by_name(db_session, "Hiking")
    make_event(db_session, group_by_name(db_session, "Runners Club"))
    db_session.commit()

    by_interest = client.get("/api/v1/groups", headers=STUDENT, params={"interest_id": hiking.id})
    with_events = client.get(
        "/api/v1/groups", headers=STUDENT, params={"has_upcoming_events": True}
    )

    assert {g["name"] for g in by_interest.json()["items"]} == {
        "Viajeros Uniandes",
        "Runners Club",
        "Senderismo Andino",
    }
    [runners] = with_events.json()["items"]
    assert runners["name"] == "Runners Club"
    assert runners["next_event"]["title"] == "Runners Club meetup"


def test_group_detail_is_logged_with_entry_point(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Tennis Uniandes")

    response = client.get(
        f"/api/v1/groups/{group.id}", headers=STUDENT, params={"entry_point": "recommendation"}
    )

    assert response.status_code == 200
    assert response.json()["meeting_building"]["code"] == "CD"
    [view] = _events(db_session, "group_viewed")
    assert view.properties["entry_point"] == "recommendation"
    assert view.properties["profile"]["has_image"] is True


def test_missing_group_returns_404(client: TestClient) -> None:
    assert client.get("/api/v1/groups/99999", headers=STUDENT).status_code == 404


def test_save_and_unsave_are_idempotent(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Coro Uniandes")

    client.put(f"/api/v1/groups/{group.id}/save", headers=STUDENT)
    client.put(f"/api/v1/groups/{group.id}/save", headers=STUDENT)
    saved = client.get("/api/v1/me/saved-groups", headers=STUDENT).json()
    client.delete(f"/api/v1/groups/{group.id}/save", headers=STUDENT)

    assert [g["name"] for g in saved] == ["Coro Uniandes"]
    assert saved[0]["is_saved"] is True
    assert client.get("/api/v1/me/saved-groups", headers=STUDENT).json() == []
    assert len(_events(db_session, "group_saved")) == 1


def test_join_leave_and_rejoin(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Runners Club")
    url = f"/api/v1/groups/{group.id}/join"

    first = client.post(url, headers=STUDENT, json={"entry_point": "search"})
    again = client.post(url, headers=STUDENT, json={"entry_point": "explore"})
    client.delete(f"/api/v1/groups/{group.id}/membership", headers=STUDENT)
    rejoin = client.post(url, headers=STUDENT, json={"entry_point": "recommendation"})

    assert first.json()["entry_point"] == "search"
    assert again.json()["entry_point"] == "search"
    assert rejoin.json()["entry_point"] == "recommendation"
    memberships = db_session.scalars(
        select(Membership).where(Membership.group_id == group.id)
    ).all()
    assert len(memberships) == 1
    assert memberships[0].status == MembershipStatus.ACTIVE
    my_groups = client.get("/api/v1/me/groups", headers=STUDENT).json()
    assert [g["name"] for g in my_groups] == ["Runners Club"]
    assert len(_events(db_session, "group_joined")) == 2


def test_cannot_join_inactive_group(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Club de Ajedrez")

    response = client.post(f"/api/v1/groups/{group.id}/join", headers=STUDENT, json={})

    assert response.status_code == 409


def test_create_group_makes_creator_admin(client: TestClient, db_session: Session) -> None:
    tag = interest_by_name(db_session, "Cybersecurity")
    payload = {
        "name": "Ciberseguridad Uniandes",
        "category_id": tag.category_id,
        "description": "CTF practice, security talks and responsible disclosure workshops.",
        "tag_ids": [tag.id],
    }

    created = client.post("/api/v1/groups", headers=STUDENT, json=payload)
    duplicate = client.post("/api/v1/groups", headers=STUDENT, json=payload)
    bad_tag = client.post(
        "/api/v1/groups", headers=STUDENT, json={**payload, "name": "Other", "tag_ids": [99999]}
    )

    assert created.status_code == 201
    assert created.json()["my_role"] == "admin"
    assert created.json()["founded_year"] == datetime.now(UTC).year
    assert created.json()["member_count"] == 1
    assert duplicate.status_code == 409
    assert bad_tag.status_code == 422


def test_only_admins_can_update_and_last_admin_cannot_leave(
    client: TestClient, db_session: Session
) -> None:
    group = group_by_name(db_session, "Basket Andes")
    admin = make_user(db_session, "captain@uniandes.edu.co")
    add_member(db_session, admin, group, MembershipRole.ADMIN)
    db_session.commit()
    admin_headers = auth_header("captain@uniandes.edu.co")
    change = {"instagram_url": "https://instagram.com/basketandes"}

    forbidden = client.patch(f"/api/v1/groups/{group.id}", headers=STUDENT, json=change)
    allowed = client.patch(f"/api/v1/groups/{group.id}", headers=admin_headers, json=change)
    leave = client.delete(f"/api/v1/groups/{group.id}/membership", headers=admin_headers)

    assert forbidden.status_code == 403
    assert allowed.json()["instagram_url"] == "https://instagram.com/basketandes"
    assert leave.status_code == 409


def test_past_events_do_not_count_as_upcoming(client: TestClient, db_session: Session) -> None:
    group = group_by_name(db_session, "Cineclub Uniandes")
    make_event(db_session, group, starts_in=-timedelta(days=1))
    db_session.commit()

    detail = client.get(f"/api/v1/groups/{group.id}", headers=STUDENT).json()

    assert detail["next_event"] is None
    assert detail["upcoming_events"] == []
