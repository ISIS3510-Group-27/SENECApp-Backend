"""Analytics event recording: client batches and server-side domain events."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.client_context import ClientContext
from app.models import AnalyticsEvent
from app.schemas.analytics import ClientEventIn

# Client clocks drift; anything further in the future than this is clamped.
MAX_CLOCK_SKEW = timedelta(minutes=5)


def track(
    db: Session,
    name: str,
    *,
    user_id: int | None,
    context: ClientContext | None = None,
    properties: dict[str, Any] | None = None,
    screen: str | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """Record a server-side event in the caller's transaction (committed with it)."""
    context = context or ClientContext()
    db.add(
        AnalyticsEvent(
            name=name,
            source="server",
            user_id=user_id,
            session_id=context.session_id,
            app=context.app,
            app_version=context.app_version,
            platform=context.platform,
            device_model=context.device_model,
            os_version=context.os_version,
            screen=screen,
            occurred_at=occurred_at or datetime.now(UTC),
            properties=properties or {},
        )
    )


def ingest_client_events(
    db: Session, events: list[ClientEventIn], user_id: int | None
) -> tuple[int, int]:
    """Store a client batch; events already received (same ``event_id``) are skipped.

    Returns ``(accepted, duplicates)``.
    """
    now = datetime.now(UTC)
    rows = [
        {
            "event_id": event.event_id,
            "name": event.name,
            "source": "client",
            "user_id": user_id,
            "session_id": event.session_id,
            "app": event.app,
            "app_version": event.app_version,
            "platform": event.platform,
            "device_model": event.device_model,
            "os_version": event.os_version,
            "screen": event.screen,
            "occurred_at": min(_as_utc(event.occurred_at), now + MAX_CLOCK_SKEW),
            "properties": event.properties,
        }
        for event in events
    ]
    stmt = (
        pg_insert(AnalyticsEvent)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(AnalyticsEvent.id)
    )
    accepted = len(db.execute(stmt).all())
    db.commit()
    return accepted, len(rows) - accepted


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
