from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import ClientCtx, CurrentUser, DbSession
from app.models import GroupMessage
from app.schemas.message import MessageIn, MessageRead
from app.services import messages as message_service

router = APIRouter(prefix="/groups/{group_id}/messages", tags=["messages"])


@router.get("")
def list_messages(
    group_id: int,
    user: CurrentUser,
    db: DbSession,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[MessageRead]:
    """Group chat, newest first. Pass the last ``id`` as ``before_id`` to load older ones."""
    rows = message_service.list_messages(db, user, group_id, before_id, limit)
    return [_read(message, author_name) for message, author_name in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def post_message(
    group_id: int, message: MessageIn, user: CurrentUser, db: DbSession, context: ClientCtx
) -> MessageRead:
    """Post to the group chat (members). Other members are notified."""
    created = message_service.post_message(db, user, group_id, message.body, context)
    return _read(created, user.full_name)


def _read(message: GroupMessage, author_name: str | None) -> MessageRead:
    return MessageRead(
        id=message.id,
        group_id=message.group_id,
        author_id=message.author_id,
        author_name=author_name if message.author_id else "SENECApp",
        body=message.body,
        kind=message.kind.value,
        created_at=message.created_at,
    )
