from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import ClientCtx, CurrentUser, DbSession
from app.schemas.notification import DeviceIn, NotificationPage, NotificationRead
from app.services import notifications as notification_service

router = APIRouter(prefix="/me", tags=["notifications"])


@router.get("/notifications")
def list_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationPage:
    items, total, unread = notification_service.list_notifications(
        db, user, unread_only, limit, offset
    )
    return NotificationPage(
        items=[NotificationRead.model_validate(n) for n in items],
        total=total,
        limit=limit,
        offset=offset,
        unread_count=unread,
    )


@router.post("/notifications/{notification_id}/open")
def open_notification(
    notification_id: int, user: CurrentUser, db: DbSession, context: ClientCtx
) -> NotificationRead:
    """Call when the user taps a notification (push or in-app). Counts as interaction (BQ8)."""
    notification = notification_service.mark_opened(db, user, notification_id, context)
    return NotificationRead.model_validate(notification)


@router.post("/notifications/{notification_id}/dismiss")
def dismiss_notification(
    notification_id: int, user: CurrentUser, db: DbSession, context: ClientCtx
) -> NotificationRead:
    """Call when the user swipes a notification away without opening it."""
    notification = notification_service.mark_dismissed(db, user, notification_id, context)
    return NotificationRead.model_validate(notification)


@router.post("/devices", status_code=status.HTTP_204_NO_CONTENT)
def register_device(device: DeviceIn, user: CurrentUser, db: DbSession) -> Response:
    """Register (or refresh) this device's FCM token for push notifications."""
    notification_service.register_device(db, user, device.token, device.platform, device.app)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/devices/{token}", status_code=status.HTTP_204_NO_CONTENT)
def unregister_device(token: str, user: CurrentUser, db: DbSession) -> Response:
    """Call on sign-out so the device stops receiving this user's notifications."""
    notification_service.unregister_device(db, user, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
