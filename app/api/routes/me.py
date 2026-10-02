from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.user import InterestsUpdate, UserRead, UserUpdate
from app.services import users as user_service

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
def read_me(user: CurrentUser) -> UserRead:
    """The signed-in student's profile. The first call after sign-in creates it."""
    return UserRead.from_user(user)


@router.patch("")
def update_me(changes: UserUpdate, user: CurrentUser, db: DbSession) -> UserRead:
    user_service.update_profile(db, user, changes)
    return UserRead.from_user(user_service.get_user(db, user.id))


@router.put("/interests")
def replace_my_interests(body: InterestsUpdate, user: CurrentUser, db: DbSession) -> UserRead:
    try:
        user_service.replace_interests(db, user, body.interest_ids)
    except user_service.UnknownInterestsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return UserRead.from_user(user_service.get_user(db, user.id))
