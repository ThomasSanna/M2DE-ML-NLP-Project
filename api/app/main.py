import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.routes import answer, health, ingest, memory
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_sessionmaker
from app.services.embeddings import EmbeddingError, OpenAIEmbeddingClient
from app.services.glpi import GlpiError, GlpiRestClient
from app.services.llm import LLMError, TGIChatClient
from app.services.reranker import HttpRerankerClient, RerankerError
from app.services.websearch import EXA_BASE_URL, ExaClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.glpi_webhook_secret is None:
        logger.warning("GLPI_WEBHOOK_SECRET is empty: GLPI webhook signatures are not verified")

    engine = create_engine(settings.database_url)
    app.state.sessionmaker = create_sessionmaker(engine)

    headers = {}
    if settings.embedding_api_key is not None:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key.get_secret_value()}"
    async with AsyncExitStack() as stack:
        embeddings_http = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=str(settings.embedding_base_url),
                headers=headers,
                timeout=settings.embedding_timeout_seconds,
            )
        )
        app.state.embedder = OpenAIEmbeddingClient(
            embeddings_http, settings.embedding_model, settings.embedding_instruction
        )
        llm_http = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=str(settings.llm_base_url), timeout=settings.llm_timeout_seconds
            )
        )
        app.state.chat_model = TGIChatClient(llm_http, settings.llm_model, settings.llm_max_tokens)
        reranker_http = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=str(settings.reranker_base_url),
                timeout=settings.reranker_timeout_seconds,
            )
        )
        app.state.reranker = HttpRerankerClient(reranker_http)
        if settings.exa_api_key is None:
            logger.warning("EXA_API_KEY is empty: web search is disabled")
            app.state.web_search = None
        else:
            exa_http = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=EXA_BASE_URL,
                    headers={"x-api-key": settings.exa_api_key.get_secret_value()},
                    timeout=settings.exa_timeout_seconds,
                )
            )
            app.state.web_search = ExaClient(
                exa_http, settings.exa_num_results, settings.exa_include_domains
            )
        if settings.glpi_user_token is None:
            logger.warning("GLPI_USER_TOKEN is empty: answering GLPI tickets is disabled")
            app.state.glpi = None
        else:
            glpi_http = await stack.enter_async_context(
                httpx.AsyncClient(base_url=str(settings.glpi_api_url), timeout=30.0)
            )
            app.state.glpi = GlpiRestClient(
                glpi_http,
                settings.glpi_user_token.get_secret_value(),
                settings.glpi_app_token.get_secret_value() if settings.glpi_app_token else None,
            )
        yield

    await engine.dispose()


def _bad_gateway(detail: str) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Handler for a remote model failure: the cause is logged, never returned."""

    async def handler(_: Request, exc: Exception) -> JSONResponse:
        logger.error("%s", exc)
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": detail})

    return handler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Memory API - GLPI chatbot PoC",
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
    app.add_exception_handler(EmbeddingError, _bad_gateway("Embeddings server unavailable"))
    app.add_exception_handler(RerankerError, _bad_gateway("Reranker unavailable"))
    app.add_exception_handler(LLMError, _bad_gateway("Language model unavailable"))
    app.add_exception_handler(GlpiError, _bad_gateway("GLPI unavailable"))
    return app
