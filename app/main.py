from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.errors import ServiceError

DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    scheduler = None
    if get_settings().scheduler_enabled:
        from app.jobs.scheduler import start_scheduler

        scheduler = start_scheduler()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging()

    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceError)
    def handle_service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> FileResponse:
        """Business-question dashboard (data endpoints require an admin token)."""
        return FileResponse(DASHBOARD)

    return app


app = create_app()
