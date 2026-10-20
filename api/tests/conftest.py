import asyncio
import hashlib
import math
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from app.api.deps import get_embedder
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app
from app.models import EMBEDDING_DIM

API_KEY = "test-api-key"
WEBHOOK_SECRET = "test-webhook-secret"
API_ROOT = Path(__file__).resolve().parents[1]


class FakeEmbedder:
    """Deterministic embeddings (hashed bag of words): similar texts give similar vectors,
    without a model server."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._bag_of_words(text) for text in texts]

    @staticmethod
    def _bag_of_words(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for word in text.lower().split():
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


def run_sql(settings: Settings, statement: str) -> None:
    async def _run() -> None:
        engine = create_engine(settings.database_url)
        async with engine.begin() as connection:
            await connection.execute(text(statement))
        await engine.dispose()

    asyncio.run(_run())


@pytest.fixture(scope="session")
def settings() -> Iterator[Settings]:
    with PostgresContainer("pgvector/pgvector:0.8.0-pg17", driver=None) as postgres:
        test_settings = Settings(
            _env_file=None,
            environment="test",
            postgres_host=postgres.get_container_host_ip(),
            postgres_port=int(postgres.get_exposed_port(5432)),
            postgres_user=postgres.username,
            postgres_password=postgres.password,
            postgres_db=postgres.dbname,
            api_key=API_KEY,
            glpi_webhook_secret=WEBHOOK_SECRET,
            embedding_base_url="http://embeddings.invalid/v1",
        )
        alembic_config = Config(API_ROOT / "alembic.ini")
        alembic_config.attributes["settings"] = test_settings
        alembic_config.attributes["configure_logger"] = False
        command.upgrade(alembic_config, "head")
        yield test_settings


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_embedder] = FakeEmbedder
    with TestClient(app) as test_client:
        yield test_client
    run_sql(settings, "TRUNCATE knowledge, feedback, glpi_event RESTART IDENTITY CASCADE")


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
