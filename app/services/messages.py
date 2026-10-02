"""Group chat."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.client_context import ClientContext
from app.models import GroupMessage, MessageKind, StudentGroup, User
from app.services import notifications
from app.services.analytics import track
from app.services.errors import NotFoundError
from app.services.groups import require_group_member


def list_messages(
    db: Session, user: User, group_id: int, before_id: int | None, limit: int
) -> list[tuple[GroupMessage, str | None]]:
    """Messages newest first, paginated with ``before_id``. Members only."""
    _get_group(db, group_id)
    require_group_member(db, user, group_id)
    stmt = (
        select(GroupMessage, User.full_name)
        .outerjoin(User, User.id == GroupMessage.author_id)
        .where(GroupMessage.group_id == group_id)
    )
    if before_id is not None:
        stmt = stmt.where(GroupMessage.id < before_id)
    rows = db.execute(stmt.order_by(GroupMessage.id.desc()).limit(limit)).all()
    return [(message, author_name) for message, author_name in rows]


def post_message(
    db: Session, user: User, group_id: int, body: str, context: ClientContext | None
) -> GroupMessage:
    group = _get_group(db, group_id)
    require_group_member(db, user, group_id)
    message = GroupMessage(group_id=group_id, author_id=user.id, body=body, kind=MessageKind.USER)
    db.add(message)
    db.flush()
    track(
        db,
        "group_message_posted",
        user_id=user.id,
        context=context,
        screen="chat",
        properties={"group_id": group_id, "message_id": message.id, "length": len(body)},
    )
    db.commit()
    notifications.notify_group_message(db, group, message.id, user.full_name, body, user.id)
    return message


def post_system_message(db: Session, group: StudentGroup, body: str) -> GroupMessage:
    """Platform-authored message (e.g. re-engagement, BQ10). Members are notified."""
    message = GroupMessage(group_id=group.id, author_id=None, body=body, kind=MessageKind.SYSTEM)
    db.add(message)
    db.commit()
    notifications.notify_group_message(db, group, message.id, "SENECApp", body, None)
    return message


def _get_group(db: Session, group_id: int) -> StudentGroup:
    group = db.get(StudentGroup, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group
