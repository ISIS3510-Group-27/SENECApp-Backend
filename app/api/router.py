from fastapi import APIRouter

from app.api.routes import (
    admin,
    analytics,
    business_questions,
    catalog,
    events,
    groups,
    health,
    insights,
    me,
    messages,
    notifications,
    recommendations,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(catalog.router)
api_router.include_router(me.router)
api_router.include_router(groups.router)
api_router.include_router(events.router)
api_router.include_router(insights.router)
api_router.include_router(messages.router)
api_router.include_router(notifications.router)
api_router.include_router(recommendations.router)
api_router.include_router(analytics.router)
api_router.include_router(business_questions.router)
api_router.include_router(admin.router)
