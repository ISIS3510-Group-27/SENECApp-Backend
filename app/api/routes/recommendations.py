from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ClientCtx, CurrentUser, DbSession
from app.schemas.recommendation import FreeNowSuggestions, GroupRecommendations
from app.services import recommendations as recommendation_service

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/groups")
def recommend_groups(
    user: CurrentUser,
    db: DbSession,
    context: ClientCtx,
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> GroupRecommendations:
    """Personalized group ranking (interests, schedule fit, proximity, popularity).

    When the student opens or joins a recommended group, pass
    ``entry_point=recommendation`` and ``rec_request_id=<request_id>``.
    """
    return recommendation_service.recommend_groups(db, user, limit, context)


@router.get("/events/free-now")
def free_now(
    user: CurrentUser,
    db: DbSession,
    latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    at: Annotated[datetime | None, Query(description="Override 'now' (testing/demo)")] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> FreeNowSuggestions:
    """Context-aware: events the student can attend during their current (or next) free
    block, ranked by walking distance, interests and how soon they start.

    The phone location is only used if the student enabled ``location_opt_in``.
    When opening a suggested event, pass ``entry_point=free_now`` and ``rec_request_id``.
    """
    return recommendation_service.free_now_suggestions(
        db, user, latitude=latitude, longitude=longitude, at=at, limit=limit
    )
