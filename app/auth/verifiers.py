"""ID-token verification.

The mobile apps sign users in with Firebase Authentication and send the resulting
ID token as ``Authorization: Bearer <token>``. A verifier turns that token into a
:class:`VerifiedIdentity`, or raises :class:`InvalidTokenError`.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import requests
from cachecontrol import CacheControl
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import Settings, get_settings


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired or otherwise not trustworthy."""


class TokenVerificationUnavailableError(Exception):
    """The token could not be checked (e.g. Google's certificates were unreachable)."""


@dataclass(frozen=True)
class VerifiedIdentity:
    uid: str
    email: str
    email_verified: bool
    name: str | None = None
    picture: str | None = None


class TokenVerifier(Protocol):
    def verify(self, token: str) -> VerifiedIdentity: ...


class FirebaseTokenVerifier:
    """Verifies Firebase ID tokens against Google's public certificates.

    Only the Firebase project ID is needed (no service-account key): the token's
    signature, expiry, audience and issuer are all checked locally.
    """

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._issuer = f"https://securetoken.google.com/{project_id}"
        # Caches Google's public certificates between requests.
        self._request = google_requests.Request(session=CacheControl(requests.Session()))

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            claims = google_id_token.verify_firebase_token(
                token, self._request, audience=self._project_id, clock_skew_in_seconds=10
            )
        except ValueError as exc:  # bad signature, expired, wrong audience, malformed...
            raise InvalidTokenError(str(exc)) from exc
        except google_exceptions.TransportError as exc:
            raise TokenVerificationUnavailableError(str(exc)) from exc

        if claims.get("iss") != self._issuer:
            raise InvalidTokenError("Token was not issued for this Firebase project")
        email = claims.get("email")
        if not email:
            raise InvalidTokenError("Token has no email claim")
        return VerifiedIdentity(
            uid=claims["sub"],
            email=email.lower(),
            email_verified=bool(claims.get("email_verified", False)),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )


class DevTokenVerifier:
    """Local-development verifier: accepts ``dev:<email>`` without any signature.

    Lets the API be exercised from Swagger, tests and emulators before a Firebase
    project exists. Disabled in production by a settings validator.
    """

    PREFIX = "dev:"

    def verify(self, token: str) -> VerifiedIdentity:
        if not token.startswith(self.PREFIX):
            raise InvalidTokenError(f"Dev tokens must look like '{self.PREFIX}<email>'")
        email = token.removeprefix(self.PREFIX).strip().lower()
        if "@" not in email:
            raise InvalidTokenError("Dev token does not contain a valid email")
        return VerifiedIdentity(uid=f"{self.PREFIX}{email}", email=email, email_verified=True)


def build_token_verifier(settings: Settings) -> TokenVerifier:
    if settings.auth_provider == "firebase":
        return FirebaseTokenVerifier(settings.firebase_project_id)
    return DevTokenVerifier()


@lru_cache
def get_token_verifier() -> TokenVerifier:
    return build_token_verifier(get_settings())
