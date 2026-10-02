"""ORM models.

Import every model module here so that Alembic autogenerate can discover all tables
through ``Base.metadata``.
"""

from app.db.base import Base

__all__ = ["Base"]
