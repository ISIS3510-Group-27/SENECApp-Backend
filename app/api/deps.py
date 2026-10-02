from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.verifiers import (
    InvalidTokenError,
    TokenVerificationUnavailableError,
    TokenVerifier,
    get_token_verifier,
)
from app.core.client_context import ClientContext, get_client_context
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import User
from app.services import users as user_service

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ClientCtx = Annotated[ClientContext, Depends(get_client_context)]

_bearer = HTTPBearer(
    auto_error=False,
    description="Firebase ID token (or `dev:<email>` when AUTH_PROVIDER=dev).",
)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
Verifier = Annotated[TokenVerifier, Depends(get_token_verifier)]


def get_current_user(
    db: DbSession, settings: SettingsDep, verifier: Verifier, credentials: BearerCredentials
) -> User:
    if credentials is None:
        raise _unauthorized("Missing bearer token")
    return _authenticate(db, settings, verifier, credentials.credentials)


def get_optional_user(
    db: DbSession, settings: SettingsDep, verifier: Verifier, credentials: BearerCredentials
) -> User | None:
    """Like :func:`get_current_user`, but anonymous requests get ``None``.

    A token that *is* sent must still be valid.
    """
    if credentials is None:
        return None
    return _authenticate(db, settings, verifier, credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def get_admin_user(user: CurrentUser, settings: SettingsDep) -> User:
    """Platform administrators (``ADMIN_EMAILS``): analytics and operations endpoints."""
    if user.email.lower() not in settings.admin_emails_list:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


def _authenticate(db: Session, settings: Settings, verifier: TokenVerifier, token: str) -> User:
    try:
        identity = verifier.verify(token)
    except InvalidTokenError as exc:
        raise _unauthorized("Invalid or expired token") from exc
    except TokenVerificationUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is temporarily unavailable"
        ) from exc

    domain = identity.email.rsplit("@", 1)[-1]
    if domain not in settings.allowed_email_domains_list:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email domain is not allowed")
    if settings.require_verified_email and not identity.email_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email address is not verified")

    user = user_service.get_or_create_user(db, identity)
    return user_service.get_user(db, user.id)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )
