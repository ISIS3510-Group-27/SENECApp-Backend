import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import verifiers
from app.auth.verifiers import (
    DevTokenVerifier,
    FirebaseTokenVerifier,
    InvalidTokenError,
    VerifiedIdentity,
    get_token_verifier,
)
from app.core.config import Settings
from app.main import app
from app.models import User
from tests.conftest import auth_header


def test_dev_verifier_accepts_dev_token() -> None:
    identity = DevTokenVerifier().verify("dev:Ana@Uniandes.edu.co")

    assert identity.email == "ana@uniandes.edu.co"
    assert identity.uid == "dev:ana@uniandes.edu.co"
    assert identity.email_verified


@pytest.mark.parametrize("token", ["ana@uniandes.edu.co", "dev:", "dev:not-an-email"])
def test_dev_verifier_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        DevTokenVerifier().verify(token)


def test_dev_auth_is_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        Settings(_env_file=None, app_env="production", auth_provider="dev")


def test_missing_token_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_invalid_token_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"})

    assert response.status_code == 401


def test_email_outside_allowed_domain_returns_403(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers=auth_header("someone@gmail.com"))

    assert response.status_code == 403


def test_first_sign_in_creates_user_once(client: TestClient, db_session: Session) -> None:
    first = client.get("/api/v1/me", headers=auth_header("new.student@uniandes.edu.co"))
    second = client.get("/api/v1/me", headers=auth_header("new.student@uniandes.edu.co"))

    assert first.status_code == 200
    assert first.json()["email"] == "new.student@uniandes.edu.co"
    assert first.json()["full_name"] == "new.student"
    assert first.json()["id"] == second.json()["id"]
    count = len(
        db_session.scalars(select(User).where(User.email == "new.student@uniandes.edu.co")).all()
    )
    assert count == 1


def test_sign_in_links_existing_user_by_email(client: TestClient, db_session: Session) -> None:
    existing = User(email="seeded@uniandes.edu.co", full_name="Seeded Student")
    db_session.add(existing)
    db_session.commit()

    response = client.get("/api/v1/me", headers=auth_header("seeded@uniandes.edu.co"))

    assert response.json()["id"] == existing.id
    assert response.json()["full_name"] == "Seeded Student"
    db_session.refresh(existing)
    assert existing.firebase_uid == "dev:seeded@uniandes.edu.co"


FIREBASE_PROJECT = "demo-senecapp"


def _firebase_claims(**overrides: object) -> dict[str, object]:
    claims = {
        "iss": f"https://securetoken.google.com/{FIREBASE_PROJECT}",
        "sub": "firebase-uid-123",
        "email": "Ana@Uniandes.edu.co",
        "email_verified": True,
        "name": "Ana María",
    }
    return {**claims, **overrides}


def _stub_google_verification(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    def fake_verify(*_args: object, **_kwargs: object) -> object:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(verifiers.google_id_token, "verify_firebase_token", fake_verify)


def test_firebase_verifier_maps_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_google_verification(monkeypatch, _firebase_claims())

    identity = FirebaseTokenVerifier(FIREBASE_PROJECT).verify("token")

    assert identity.uid == "firebase-uid-123"
    assert identity.email == "ana@uniandes.edu.co"
    assert identity.name == "Ana María"
    assert identity.email_verified


@pytest.mark.parametrize(
    "result",
    [
        ValueError("Token expired"),
        _firebase_claims(iss="https://securetoken.google.com/another-project"),
        _firebase_claims(email=None),
    ],
)
def test_firebase_verifier_rejects_untrusted_tokens(
    monkeypatch: pytest.MonkeyPatch, result: object
) -> None:
    _stub_google_verification(monkeypatch, result)

    with pytest.raises(InvalidTokenError):
        FirebaseTokenVerifier(FIREBASE_PROJECT).verify("token")


def test_firebase_provider_requires_project_id() -> None:
    with pytest.raises(ValidationError, match="FIREBASE_PROJECT_ID"):
        Settings(_env_file=None, auth_provider="firebase")


def test_unverified_email_returns_403(client: TestClient) -> None:
    class UnverifiedVerifier:
        def verify(self, token: str) -> VerifiedIdentity:
            return VerifiedIdentity(uid="u1", email="ana@uniandes.edu.co", email_verified=False)

    app.dependency_overrides[get_token_verifier] = UnverifiedVerifier

    response = client.get("/api/v1/me", headers={"Authorization": "Bearer anything"})

    assert response.status_code == 403
