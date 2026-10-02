from pydantic import BaseModel


class Page[T](BaseModel):
    """A page of results plus the total number of matches."""

    items: list[T]
    total: int
    limit: int
    offset: int
