import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.embeddings import Embedder


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_embedder(request: Request) -> Embedder:
    return request.app.state.embedder


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    settings: SettingsDep, api_key: Annotated[str | None, Security(_api_key_header)]
) -> None:
    expected = settings.api_key.get_secret_value().encode()
    if api_key is None or not secrets.compare_digest(api_key.encode(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé API absente ou invalide")
