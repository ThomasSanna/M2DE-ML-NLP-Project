import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.routes import health, ingest, memory
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_sessionmaker
from app.services.embeddings import EmbeddingError, OpenAIEmbeddingClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.glpi_webhook_secret is None:
        logger.warning("GLPI_WEBHOOK_SECRET vide : signature des webhooks GLPI non vérifiée")

    engine = create_engine(settings.database_url)
    app.state.sessionmaker = create_sessionmaker(engine)

    headers = {}
    if settings.embedding_api_key is not None:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key.get_secret_value()}"
    async with httpx.AsyncClient(
        base_url=str(settings.embedding_base_url),
        headers=headers,
        timeout=settings.embedding_timeout_seconds,
    ) as http:
        app.state.embedder = OpenAIEmbeddingClient(
            http, settings.embedding_model, settings.embedding_instruction
        )
        yield

    await engine.dispose()


async def _embedding_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.error("%s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Serveur d'embeddings indisponible"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="API mémoire — PoC chatbot GLPI",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.environment == "prod" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(ingest.router)
    api_v1.include_router(memory.router)
    app.include_router(health.router)
    app.include_router(api_v1)
    app.add_exception_handler(EmbeddingError, _embedding_error_handler)
    return app
