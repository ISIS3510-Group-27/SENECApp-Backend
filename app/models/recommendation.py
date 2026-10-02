import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import enum_check, str_enum
from app.models.mixins import CreatedAtMixin


class RecommendationKind(enum.StrEnum):
    GROUP = "group"
    EVENT = "event"


class RecommendationLog(CreatedAtMixin, Base):
    """One item shown in a recommendation list, with the features used to rank it.

    These rows are both the recommender's training data and the input of BQ2
    (groups) and BQ3 (free-block event suggestions).
    """

    __tablename__ = "recommendation_logs"
    __table_args__ = (
        Index("ix_recommendation_logs_kind_created_at", "kind", "created_at"),
        Index("ix_recommendation_logs_user_id_item_id", "user_id", "item_id"),
        enum_check("kind", RecommendationKind),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[RecommendationKind] = mapped_column(str_enum(RecommendationKind))
    item_id: Mapped[int] = mapped_column(Integer)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    model_version: Mapped[str] = mapped_column(String(40))


class RecommenderModel(Base):
    """A trained version of the group recommender's weights."""

    __tablename__ = "recommender_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(40), unique=True)
    weights: Mapped[dict[str, float]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
