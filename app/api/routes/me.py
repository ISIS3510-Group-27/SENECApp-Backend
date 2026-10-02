from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.group import GroupSummary
from app.schemas.schedule import ScheduleBlockRead, ScheduleIn
from app.schemas.user import InterestsUpdate, UserRead, UserUpdate
from app.services import groups as group_service
from app.services import schedule as schedule_service
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


@router.get("/groups")
def my_groups(user: CurrentUser, db: DbSession) -> list[GroupSummary]:
    """Groups the student is an active member of, most recently joined first."""
    return group_service.list_user_groups(db, user)


@router.get("/saved-groups")
def my_saved_groups(user: CurrentUser, db: DbSession) -> list[GroupSummary]:
    return group_service.list_saved_groups(db, user)


@router.get("/schedule")
def my_schedule(user: CurrentUser, db: DbSession) -> list[ScheduleBlockRead]:
    """Weekly class blocks (local campus time)."""
    return [ScheduleBlockRead.model_validate(b) for b in schedule_service.get_schedule(db, user.id)]


@router.put("/schedule")
def replace_my_schedule(
    schedule: ScheduleIn, user: CurrentUser, db: DbSession
) -> list[ScheduleBlockRead]:
    """Replace the weekly class schedule. Free time between classes powers context-aware
    suggestions and recommendation schedule fit."""
    blocks = schedule_service.replace_schedule(db, user, schedule)
    return [ScheduleBlockRead.model_validate(b) for b in blocks]
