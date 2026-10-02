from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.analytics.questions import QUESTIONS, answer_question
from app.api.deps import AdminUser, DbSession

router = APIRouter(prefix="/analytics/bq", tags=["business questions"])


@router.get("")
def list_questions(_admin: AdminUser) -> list[dict[str, Any]]:
    return [{"id": q.id, "type": q.type, "question": q.question} for q in QUESTIONS.values()]


@router.get("/{question_id}")
def get_answer(
    question_id: str,
    _admin: AdminUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365, description="Look-back window")] = 90,
    app: Literal["flutter", "kotlin"] | None = None,
    user_id: Annotated[
        int | None, Query(description="BQ2 only: include this student's join predictions")
    ] = None,
) -> dict[str, Any]:
    """Answer a business question from the analytics data (administrators)."""
    if question_id not in QUESTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown question. Use {sorted(QUESTIONS)}")
    return answer_question(db, question_id, days, app, user_id)
