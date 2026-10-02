from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)


class MessageRead(BaseModel):
    id: int
    group_id: int
    author_id: int | None
    author_name: str | None
    body: str
    kind: str
    created_at: datetime
