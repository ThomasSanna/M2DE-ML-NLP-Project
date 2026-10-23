import asyncio
import copy
import hashlib
import hmac
import json
import math
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from app.api.deps import get_chat_model, get_embedder, get_reranker, get_web_search
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app
from app.models import EMBEDDING_DIM
from app.services.embeddings import EmbeddingError
from app.services.glpi import GlpiError, GlpiTicket
from app.services.llm import ChatMessage, Completion, LLMError
from app.services.reranker import RerankerError
from app.services.websearch import WebResult, WebSearchError

API_KEY = "test-api-key"
WEBHOOK_SECRET = "test-webhook-secret"
API_ROOT = Path(__file__).resolve().parents[1]
TICKET = GlpiTicket(
    id=42, title="Wifi eduroam", content="Le wifi eduroam ne marche pas sur mon Mac"
)


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


class FakeReranker:
    """Accepts every candidate (sigmoid(5.0) ~ 0.993), without a model server."""

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [5.0] * len(documents)


class ScriptedChatModel:
    """Returns scripted outputs in order (100 prompt and 10 completion tokens each) and
    records a deep copy of every call, so later appends cannot rewrite history."""

    def __init__(self, outputs: list[str | Completion]) -> None:
        self._outputs = list(outputs)
        self.calls: list[tuple[list[ChatMessage], dict[str, object]]] = []

    async def complete(self, messages: list[ChatMessage], schema: dict[str, object]) -> Completion:
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(schema)))
        output = self._outputs.pop(0)
        return output if isinstance(output, Completion) else Completion(output, "stop", 100, 10)


class FakeWebSearch:
    def __init__(self, results: list[WebResult] | None = None, fail: bool = False) -> None:
        self._results = results or []
        self._fail = fail
        self.queries: list[str] = []

    async def search(self, query: str) -> list[WebResult]:
        self.queries.append(query)
        if self._fail:
            raise WebSearchError("Exa unreachable")
        return self._results


class FakeGlpi:
    """Serves TICKET and records the followups, without a GLPI server."""

    def __init__(self, fail_read: bool = False, fail_followup: bool = False) -> None:
        self._fail_read = fail_read
        self._fail_followup = fail_followup
        self.followups: list[tuple[int, str, bool]] = []

    async def get_ticket(self, ticket_id: int) -> GlpiTicket | None:
        if self._fail_read:
            raise GlpiError("GLPI down")
        return TICKET if ticket_id == TICKET.id else None

    async def add_followup(self, ticket_id: int, text: str, *, private: bool) -> int:
        if self._fail_followup:
            raise GlpiError("GLPI down")
        self.followups.append((ticket_id, text, private))
        return 7


class FailingEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("embeddings down")


class FailingReranker:
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RerankerError("reranker down")


class FailingChatModel:
    async def complete(self, messages: list[ChatMessage], schema: dict[str, object]) -> Completion:
        raise LLMError("boom")


def override(client: TestClient, dependency: object, value: object) -> None:
    # TestClient.app is typed as ASGIApp, not FastAPI.
    client.app.dependency_overrides[dependency] = lambda: value  # type: ignore[attr-defined]


def glpi_headers(body: bytes, timestamp: str = "1760000000") -> dict[str, str]:
    """Headers as GLPI 11 computes them: hmac_sha256(body . timestamp, secret)."""
    signature = hmac.new(WEBHOOK_SECRET.encode(), body + timestamp.encode(), hashlib.sha256)
    return {
        "Content-Type": "application/json",
        "X-GLPI-signature": signature.hexdigest(),
        "X-GLPI-timestamp": timestamp,
    }


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
            llm_base_url="http://llm.invalid/v1",
            reranker_base_url="http://reranker.invalid",
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
    app.dependency_overrides[get_reranker] = FakeReranker
    app.dependency_overrides[get_chat_model] = lambda: ScriptedChatModel(
        [json.dumps({"action": "answer", "answer": "Réponse", "sources": []})] * 3
    )
    app.dependency_overrides[get_web_search] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    run_sql(settings, "TRUNCATE knowledge, feedback, glpi_event RESTART IDENTITY CASCADE")


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
