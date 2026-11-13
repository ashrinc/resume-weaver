from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import tailor
from app.archive import routes as archive
from app.archive.service import ArchiveService
from app.archive.storage import build_storage
from app.config import get_settings
from app.llm.client import GeminiClient
from app.tailor.pipeline import TailorPipeline


def create_app(
    *,
    tailor_pipeline: TailorPipeline | None = None,
    archive_service: ArchiveService | None = None,
    tailor_deadline_seconds: float | None = None,
) -> FastAPI:
    """Build the app. Tests inject a pipeline with a fake Gemini client, an
    archive service pointed at temporary directories, and a short
    ``tailor_deadline_seconds`` (default: TAILOR_DEADLINE_SECONDS)."""
    settings = get_settings()
    archive_svc = archive_service or ArchiveService(build_storage(settings), settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await archive_svc.init()
        yield
        await archive_svc.close()

    app = FastAPI(title="Resume Tailor API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        # Lets the browser read the original file name on archive downloads.
        expose_headers=["Content-Disposition"],
    )
    register_error_handlers(app)
    # The Gemini client is created on first use, so the app starts without a key.
    app.state.tailor_pipeline = tailor_pipeline or TailorPipeline(client_factory=GeminiClient)
    app.state.archive_service = archive_svc
    app.state.tailor_deadline_seconds = (
        tailor_deadline_seconds
        if tailor_deadline_seconds is not None
        else settings.tailor_deadline_seconds
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(tailor.router)
    app.include_router(archive.router)
    return app


app = create_app()
