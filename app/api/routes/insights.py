from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.insights import Audience, BestTimes
from app.services import insights as insight_service

router = APIRouter(prefix="/groups", tags=["leader insights"])


@router.get("/{group_id}/insights/best-times")
def get_best_times(
    group_id: int,
    user: CurrentUser,
    db: DbSession,
    duration_minutes: Annotated[int, Query(ge=30, le=240, multiple_of=30)] = 120,
    limit: Annotated[int, Query(ge=1, le=5)] = 3,
) -> BestTimes:
    return insight_service.best_times(db, user, group_id, duration_minutes, limit)


@router.get("/{group_id}/insights/audience")
def get_audience(
    group_id: int,
    user: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
) -> Audience:
    return insight_service.audience(db, user, group_id, days)
