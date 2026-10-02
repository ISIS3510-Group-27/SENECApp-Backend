"""Notifications: in-app inbox, push delivery and interaction tracking (BQ8)."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.client_context import ClientContext
from app.models import (
    DeviceToken,
    Interest,
    Membership,
    MembershipStatus,
    Notification,
    NotificationType,
    PushStatus,
    StudentGroup,
    User,
    UserInterest,
)
from app.services.analytics import track
from app.services.errors import NotFoundError
from app.services.push import PushMessage, get_push_sender

# Cap for broadcast-style notifications (e.g. "a new group you might like").
MAX_RECOMMENDATION_RECIPIENTS = 500


def notify(
    db: Session,
    user_ids: Iterable[int],
    type_: NotificationType,
    title: str,
    body: str,
    *,
    group_id: int | None = None,
    event_id: int | None = None,
    data: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> list[Notification]:
    """Create one notification per user (respecting their opt-in) and push it.

    Commits. Call it after the action that triggered it has been committed.
    """
    recipients = list(
        db.scalars(select(User.id).where(User.id.in_(set(user_ids)), User.notifications_opt_in))
    )
    if not recipients:
        return []

    notifications = [
        Notification(
            user_id=user_id,
            type=type_,
            title=title,
            body=body,
            group_id=group_id,
            event_id=event_id,
            data=data or {},
            **({"created_at": created_at} if created_at else {}),
        )
        for user_id in recipients
    ]
    db.add_all(notifications)
    db.flush()
    _push(db, notifications)
    db.commit()
    return notifications


def _push(db: Session, notifications: list[Notification]) -> None:
    sender = get_push_sender()
    if not sender.enabled:
        return
    tokens_by_user: dict[int, list[str]] = defaultdict(list)
    for user_id, token in db.execute(
        select(DeviceToken.user_id, DeviceToken.token).where(
            DeviceToken.user_id.in_({n.user_id for n in notifications})
        )
    ):
        tokens_by_user[user_id].append(token)

    messages, owners = [], []
    for notification in notifications:
        payload = {
            "notification_id": str(notification.id),
            "type": notification.type.value,
            **{k: str(v) for k, v in notification.data.items() if v is not None},
        }
        for token in tokens_by_user.get(notification.user_id, []):
            messages.append(PushMessage(token, notification.title, notification.body, payload))
            owners.append(notification)
    if not messages:
        return

    result = sender.send(messages)
    # A notification counts as pushed if it reached at least one of the user's devices.
    for message, notification in zip(messages, owners, strict=True):
        if message.token in result.delivered_tokens:
            notification.push_status = PushStatus.SENT
        elif notification.push_status != PushStatus.SENT:
            notification.push_status = PushStatus.FAILED
    if result.invalid_tokens:
        db.execute(delete(DeviceToken).where(DeviceToken.token.in_(result.invalid_tokens)))


# --- Triggers ------------------------------------------------------------------------


def active_member_ids(db: Session, group_id: int, exclude: int | None = None) -> list[int]:
    stmt = select(Membership.user_id).where(
        Membership.group_id == group_id, Membership.status == MembershipStatus.ACTIVE
    )
    if exclude is not None:
        stmt = stmt.where(Membership.user_id != exclude)
    return list(db.scalars(stmt))


def notify_new_event(
    db: Session, event_id: int, group: StudentGroup, title: str, author_id: int
) -> None:
    notify(
        db,
        active_member_ids(db, group.id, exclude=author_id),
        NotificationType.NEW_EVENT,
        title=f"New event from {group.name}",
        body=title,
        group_id=group.id,
        event_id=event_id,
        data={"group_id": group.id, "event_id": event_id},
    )


def notify_new_group(db: Session, group: StudentGroup, creator_id: int | None) -> None:
    """Tell students whose interests match the new group's tags."""
    tag_ids = [tag.id for tag in group.tags]
    recipients = db.scalars(
        select(UserInterest.user_id)
        .where(UserInterest.interest_id.in_(tag_ids), UserInterest.user_id != creator_id)
        .group_by(UserInterest.user_id)
        .order_by(func.count().desc())
        .limit(MAX_RECOMMENDATION_RECIPIENTS)
    ).all()
    tag_names = db.scalars(select(Interest.name).where(Interest.id.in_(tag_ids))).all()
    notify(
        db,
        recipients,
        NotificationType.GROUP_RECOMMENDATION,
        title="A new group you might like",
        body=f"{group.name} was just created ({', '.join(sorted(tag_names))})",
        group_id=group.id,
        data={"group_id": group.id},
    )


def notify_group_message(
    db: Session,
    group: StudentGroup,
    message_id: int,
    author_name: str,
    body: str,
    author_id: int | None,
) -> None:
    notify(
        db,
        active_member_ids(db, group.id, exclude=author_id),
        NotificationType.GROUP_MESSAGE,
        title=f"{group.name}",
        body=f"{author_name}: {body[:120]}",
        group_id=group.id,
        data={"group_id": group.id, "message_id": message_id},
    )


# --- Inbox ---------------------------------------------------------------------------


def list_notifications(
    db: Session, user: User, unread_only: bool, limit: int, offset: int
) -> tuple[list[Notification], int, int]:
    """Returns ``(items, total, unread_count)``, newest first."""
    base = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        base = base.where(Notification.opened_at.is_(None))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    unread = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.opened_at.is_(None)
            )
        )
        or 0
    )
    items = db.scalars(
        base.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total, unread


def mark_opened(
    db: Session, user: User, notification_id: int, context: ClientContext | None
) -> Notification:
    notification = _own_notification(db, user, notification_id)
    if notification.opened_at is None:
        notification.opened_at = datetime.now(UTC)
        track(
            db,
            "notification_opened",
            user_id=user.id,
            context=context,
            properties=_interaction_properties(notification),
        )
        db.commit()
    return notification


def mark_dismissed(
    db: Session, user: User, notification_id: int, context: ClientContext | None
) -> Notification:
    notification = _own_notification(db, user, notification_id)
    if notification.dismissed_at is None:
        notification.dismissed_at = datetime.now(UTC)
        track(
            db,
            "notification_dismissed",
            user_id=user.id,
            context=context,
            properties=_interaction_properties(notification),
        )
        db.commit()
    return notification


def _interaction_properties(notification: Notification) -> dict[str, Any]:
    return {
        "notification_id": notification.id,
        "type": notification.type.value,
        "group_id": notification.group_id,
        "seconds_since_sent": int((datetime.now(UTC) - notification.created_at).total_seconds()),
    }


def _own_notification(db: Session, user: User, notification_id: int) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise NotFoundError("Notification not found")
    return notification


# --- Devices -------------------------------------------------------------------------


def register_device(
    db: Session, user: User, token: str, platform: str | None, app: str | None
) -> None:
    """Upsert an FCM token. A token moves to the latest user who registers it."""
    stmt = pg_insert(DeviceToken).values(user_id=user.id, token=token, platform=platform, app=app)
    stmt = stmt.on_conflict_do_update(
        index_elements=["token"],
        set_={
            "user_id": user.id,
            "platform": platform,
            "app": app,
            "last_seen_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def unregister_device(db: Session, user: User, token: str) -> None:
    db.execute(
        delete(DeviceToken).where(DeviceToken.user_id == user.id, DeviceToken.token == token)
    )
    db.commit()
