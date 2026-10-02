from fastapi import APIRouter, status

from app.api.deps import DbSession, OptionalUser
from app.schemas.analytics import EventBatchIn, EventBatchResult
from app.services import analytics as analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
def ingest_events(batch: EventBatchIn, db: DbSession, user: OptionalUser) -> EventBatchResult:
    """Receive a batch of client analytics events.

    Authentication is optional so that events from before sign-in (e.g. a crash on
    the login screen) are not lost. Re-sending a batch is safe: events are
    de-duplicated by ``event_id``.
    """
    accepted, duplicates = analytics_service.ingest_client_events(
        db, batch.events, user.id if user else None
    )
    return EventBatchResult(accepted=accepted, duplicates=duplicates)
