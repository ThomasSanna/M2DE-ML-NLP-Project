import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.embeddings import Embedder
from app.services.glpi import Glpi
from app.services.llm import ChatModel
from app.services.reranker import Reranker
from app.services.websearch import WebSearch


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_embedder(request: Request) -> Embedder:
    return request.app.state.embedder


def get_reranker(request: Request) -> Reranker:
    return request.app.state.reranker


def get_chat_model(request: Request) -> ChatModel:
    return request.app.state.chat_model


def get_web_search(request: Request) -> WebSearch | None:
    return request.app.state.web_search


def get_glpi(request: Request) -> Glpi:
    glpi = request.app.state.glpi
    if glpi is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GLPI_USER_TOKEN is not set")
    return glpi


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
RerankerDep = Annotated[Reranker, Depends(get_reranker)]
ChatModelDep = Annotated[ChatModel, Depends(get_chat_model)]
WebSearchDep = Annotated[WebSearch | None, Depends(get_web_search)]
GlpiDep = Annotated[Glpi, Depends(get_glpi)]

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    settings: SettingsDep, api_key: Annotated[str | None, Security(_api_key_header)]
) -> None:
    expected = settings.api_key.get_secret_value().encode()
    if api_key is None or not secrets.compare_digest(api_key.encode(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key")
