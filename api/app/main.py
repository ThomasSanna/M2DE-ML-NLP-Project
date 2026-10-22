import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from app.api.routes import answer, health, ingest, memory
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_sessionmaker
from app.services.embeddings import EmbeddingError, OpenAIEmbeddingClient
from app.services.glpi import GlpiError, GlpiRestClient
from app.services.llm import OpenAIChatClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.glpi_webhook_secret is None:
        logger.warning("GLPI_WEBHOOK_SECRET vide : signature des webhooks GLPI non vérifiée")

    engine = create_engine(settings.database_url)
    app.state.sessionmaker = create_sessionmaker(engine)

    async with (
        httpx.AsyncClient(
            base_url=str(settings.embedding_base_url),
            headers=_bearer(settings.embedding_api_key),
            timeout=settings.embedding_timeout_seconds,
        ) as embedding_http,
        httpx.AsyncClient(
            base_url=str(settings.llm_base_url),
            headers=_bearer(settings.llm_api_key),
            timeout=settings.llm_timeout_seconds,
        ) as llm_http,
        httpx.AsyncClient(base_url=str(settings.glpi_api_url), timeout=30.0) as glpi_http,
    ):
        app.state.embedder = OpenAIEmbeddingClient(
            embedding_http, settings.embedding_model, settings.embedding_instruction
        )
        app.state.chat_model = OpenAIChatClient(llm_http, settings.llm_model)
        app.state.glpi = None
        if settings.glpi_user_token is not None:
            app.state.glpi = GlpiRestClient(
                glpi_http,
                settings.glpi_user_token.get_secret_value(),
                settings.glpi_app_token.get_secret_value() if settings.glpi_app_token else None,
            )
        else:
            logger.warning("GLPI_USER_TOKEN vide : réponse aux tickets désactivée")
        yield

    await engine.dispose()


def _bearer(api_key: SecretStr | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key.get_secret_value()}"} if api_key else {}


async def _embedding_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.error("%s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Serveur d'embeddings indisponible"},
    )


async def _glpi_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.error("%s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": "GLPI indisponible"}
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
    api_v1.include_router(answer.router)
    app.include_router(health.router)
    app.include_router(api_v1)
    app.add_exception_handler(EmbeddingError, _embedding_error_handler)
    app.add_exception_handler(GlpiError, _glpi_error_handler)
    return app
