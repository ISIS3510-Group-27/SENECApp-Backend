import random
from datetime import UTC, datetime, time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CampusBuilding,
    Membership,
    RecommendationKind,
    RecommendationLog,
    RecommenderModel,
    ScheduleBlock,
    UserInterest,
)
from app.recommendations.model import (
    FEATURES,
    GroupProfile,
    StudentProfile,
    compute_features,
    roc_auc,
    train_logistic_regression,
)
from app.services.recommendations import train_group_model
from app.services.schedule import campus_tz
from tests.conftest import auth_header
from tests.factories import group_by_name, interest_by_name, make_event, make_user

STUDENT_EMAIL = "recs@uniandes.edu.co"
STUDENT = auth_header(STUDENT_EMAIL)


def _building(db: Session, code: str) -> CampusBuilding:
    return db.scalars(select(CampusBuilding).where(CampusBuilding.code == code)).one()


# --- Pure model ------------------------------------------------------------------------


def test_features_reward_interest_overlap_and_proximity() -> None:
    student = StudentProfile(
        interest_ids=frozenset({1, 2}),
        category_counts={10: 2},
        class_locations=((4.6030, -74.0650),),
    )
    near_match = GroupProfile(1, 10, frozenset({1, 3}), (4.6031, -74.0651), (), 50, True)
    far_other = GroupProfile(2, 20, frozenset({9}), (4.6500, -74.1000), (), 5, False)

    good = compute_features(student, near_match, max_members=50)
    bad = compute_features(student, far_other, max_members=50)

    assert good["interest_match"] == 0.5
    assert good["category_affinity"] == 1.0
    assert good["proximity"] > 0.9
    assert good["popularity"] == 1.0
    assert good["schedule_fit"] == 0.5  # unknown schedule -> neutral
    assert bad["interest_match"] == 0.0
    assert bad["proximity"] < 0.01


def test_roc_auc_handles_perfect_random_and_ties() -> None:
    assert roc_auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert roc_auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    assert roc_auc([0.5, 0.5], [0, 1]) == 0.5


def test_training_recovers_the_signal() -> None:
    rng = random.Random(1)
    rows = []
    for _ in range(2_000):
        features = {name: rng.random() for name in FEATURES}
        label = int(rng.random() < 0.05 + 0.6 * features["interest_match"] ** 2)
        rows.append((features, label))

    result = train_logistic_regression(rows)

    assert result.auc > 0.7
    assert result.weights["interest_match"] == max(result.weights[f] for f in FEATURES)


# --- API -------------------------------------------------------------------------------


def test_group_recommendations_rank_matching_groups_and_are_logged(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, STUDENT_EMAIL)
    for name in ("AI/ML", "Data Science"):
        db_session.add(
            UserInterest(user_id=user.id, interest_id=interest_by_name(db_session, name).id)
        )
    joined = group_by_name(db_session, "Open Source Uniandes")
    db_session.add(Membership(user_id=user.id, group_id=joined.id))
    db_session.commit()

    response = client.get("/api/v1/recommendations/groups", headers=STUDENT, params={"limit": 5})

    body = response.json()
    names = [item["group"]["name"] for item in body["items"]]
    assert response.status_code == 200
    assert names[0] == "AI & Machine Learning"
    assert "Open Source Uniandes" not in names
    assert any("AI/ML" in reason for reason in body["items"][0]["reasons"])
    logs = db_session.scalars(
        select(RecommendationLog).where(RecommendationLog.kind == RecommendationKind.GROUP)
    ).all()
    assert len(logs) == 5
    assert {str(log.request_id) for log in logs} == {body["request_id"]}
    assert set(logs[0].features) == set(FEATURES)


def test_free_now_suggests_nearby_events_in_the_free_block(
    client: TestClient, db_session: Session
) -> None:
    ml, cd = _building(db_session, "ML"), _building(db_session, "CD")
    user = make_user(db_session, STUDENT_EMAIL, location_opt_in=True)
    db_session.add(
        UserInterest(user_id=user.id, interest_id=interest_by_name(db_session, "Tennis").id)
    )
    # Tomorrow, local time: class until 10:00, next class at 14:00 -> free 10:00-14:00.
    tomorrow = (datetime.now(UTC).astimezone(campus_tz()) + timedelta(days=1)).date()
    for start, end in ((time(8, 30), time(10, 0)), (time(14, 0), time(15, 30))):
        db_session.add(
            ScheduleBlock(
                user_id=user.id,
                weekday=tomorrow.weekday(),
                start_time=start,
                end_time=end,
                building_id=ml.id,
            )
        )
    noon = datetime.combine(tomorrow, time(12, 0), tzinfo=campus_tz())
    tennis = group_by_name(db_session, "Tennis Uniandes")
    choir = group_by_name(db_session, "Coro Uniandes")
    in_block = make_event(db_session, tennis, building_id=cd.id, title="Tennis clinic")
    in_block.starts_at, in_block.ends_at = noon, noon + timedelta(hours=1)
    during_class = make_event(db_session, choir, building_id=ml.id, title="Choir rehearsal")
    during_class.starts_at = datetime.combine(tomorrow, time(14, 30), tzinfo=campus_tz())
    during_class.ends_at = during_class.starts_at + timedelta(hours=1)
    db_session.commit()

    at = datetime.combine(tomorrow, time(11, 0), tzinfo=campus_tz())
    response = client.get(
        "/api/v1/recommendations/events/free-now",
        headers=STUDENT,
        params={"at": at.isoformat(), "latitude": ml.latitude, "longitude": ml.longitude},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["free_block"]["minutes"] == 180
    assert body["location"] == {
        "building": {**body["location"]["building"], "code": "ML"},
        "source": "gps",
        "on_campus": True,
    }
    assert [item["event"]["title"] for item in body["items"]] == ["Tennis clinic"]
    assert body["items"][0]["walking_minutes"] >= 1
    [log] = db_session.scalars(
        select(RecommendationLog).where(RecommendationLog.kind == RecommendationKind.EVENT)
    ).all()
    assert log.context["hour"] == 11
    assert log.context["building_code"] == "ML"


def test_free_now_ignores_gps_without_consent(client: TestClient, db_session: Session) -> None:
    make_user(db_session, STUDENT_EMAIL, location_opt_in=False)
    db_session.commit()

    body = client.get(
        "/api/v1/recommendations/events/free-now",
        headers=STUDENT,
        params={"latitude": 4.6, "longitude": -74.06},
    ).json()

    assert body["location"]["source"] == "none"
    assert body["schedule_known"] is False


def test_training_needs_enough_logs(db_session: Session) -> None:
    summary = train_group_model(db_session)

    assert summary.trained is False
    assert db_session.scalars(select(RecommenderModel)).all() == []
