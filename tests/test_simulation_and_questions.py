from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.questions import QUESTIONS
from app.models import AnalyticsEvent, RecommendationLog, SeedRun, User
from app.seed.simulation import seed_simulation
from tests.conftest import auth_header

ADMIN = auth_header("admin@uniandes.edu.co")


@pytest.fixture
def simulated(db_session: Session) -> Session:
    assert seed_simulation(db_session, now=datetime.now(UTC), students=60) is True
    return db_session


def test_simulation_populates_every_source_once(simulated: Session) -> None:
    assert simulated.scalar(select(func.count(AnalyticsEvent.id))) > 1_000
    assert simulated.scalar(select(func.count(RecommendationLog.id))) > 100
    assert simulated.get(SeedRun, "simulation") is not None
    assert simulated.scalars(select(User).where(User.email == "s.arango@uniandes.edu.co")).one()
    assert seed_simulation(simulated) is False  # second run is skipped


def test_every_business_question_is_answered(client: TestClient, simulated: Session) -> None:
    listed = client.get("/api/v1/analytics/bq", headers=ADMIN).json()
    assert {q["id"] for q in listed} == set(QUESTIONS)

    for question_id in QUESTIONS:
        response = client.get(f"/api/v1/analytics/bq/{question_id}", headers=ADMIN)
        assert response.status_code == 200, question_id
        body = response.json()
        assert body["answer"], question_id
        assert body["data"], question_id


def test_planted_patterns_are_found(client: TestClient, simulated: Session) -> None:
    def answer(question_id: str) -> dict:
        return client.get(f"/api/v1/analytics/bq/{question_id}", headers=ADMIN).json()

    notifications = answer("8")["data"]["types"]
    assert notifications[0]["type"] == "group_message"
    assert notifications[-1]["type"] == "group_recommendation"
    entry_points = {r["entry_point"]: r["joins"] for r in answer("6")["data"]["entry_points"]}
    assert entry_points["recommendation"] > entry_points["search"]
    unmet = {r["interest"] for r in answer("9")["data"]["interests"] if r["unmet_demand"]}
    assert {"Cybersecurity", "Climbing", "UN Model"} <= unmet


def test_business_questions_require_admin(client: TestClient) -> None:
    student = auth_header("student@uniandes.edu.co")

    assert client.get("/api/v1/analytics/bq/1", headers=student).status_code == 403
    assert client.get("/api/v1/analytics/bq/99", headers=ADMIN).status_code == 404


def test_dashboard_is_served(client: TestClient) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "SENECApp business questions" in response.text
