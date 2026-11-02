import asyncio
import math
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.api.deps import get_embedder
from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.services import memory
from app.services.embeddings import EmbeddingError
from app.services.reranker import Reranker
from tests.conftest import FakeEmbedder, run_sql

KNOWLEDGE = [
    ("Je n'arrive plus à me connecter au wifi eduroam", "Réinstallez le profil eduroam via CAT."),
    ("Comment réinitialiser mon mot de passe ENT", "Utilisez la page « mot de passe oublié »."),
    ("L'imprimante du bâtiment B ne répond plus", "Redémarrez le spouleur d'impression."),
]


def _add(client: TestClient, auth: dict[str, str], question: str, answer: str) -> dict:
    response = client.post(
        "/api/v1/memory/knowledge",
        json={"question": question, "answer": answer, "category": "test"},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_routes_require_api_key(client: TestClient) -> None:
    assert client.post("/api/v1/memory/search", json={"query": "wifi"}).status_code == 401
    bad = {"X-API-Key": "wrong-key"}
    response = client.post("/api/v1/memory/search", json={"query": "wifi"}, headers=bad)
    assert response.status_code == 401


def test_create_then_read_knowledge(client: TestClient, auth: dict[str, str]) -> None:
    created = _add(client, auth, *KNOWLEDGE[0])
    assert created["source"] == "glpi_history"
    assert created["status"] == "active"
    assert created["reuse_count"] == 0

    response = client.get(f"/api/v1/memory/knowledge/{created['id']}", headers=auth)
    assert response.status_code == 200
    assert response.json() == created


def test_read_unknown_knowledge_returns_404(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get(f"/api/v1/memory/knowledge/{uuid.uuid4()}", headers=auth)
    assert response.status_code == 404


def test_search_ranks_closest_question_first(client: TestClient, auth: dict[str, str]) -> None:
    ids = [_add(client, auth, q, a)["id"] for q, a in KNOWLEDGE]

    response = client.post(
        "/api/v1/memory/search",
        json={"query": "impossible de me connecter au wifi", "top_k": 2},
        headers=auth,
    )
    assert response.status_code == 200
    hits = response.json()
    assert len(hits) == 2
    assert hits[0]["knowledge"]["id"] == ids[0]
    assert hits[0]["similarity"] > hits[1]["similarity"]


def test_search_ignores_inactive_knowledge(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    archived = _add(client, auth, *KNOWLEDGE[0])
    run_sql(settings, f"UPDATE knowledge SET status = 'archived' WHERE id = '{archived['id']}'")

    response = client.post("/api/v1/memory/search", json={"query": "wifi eduroam"}, headers=auth)
    assert response.json() == []


def test_search_validates_top_k(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/memory/search", json={"query": "wifi", "top_k": 0}, headers=auth
    )
    assert response.status_code == 422


def test_embedding_failure_returns_502(client: TestClient, auth: dict[str, str]) -> None:
    class FailingEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("server unreachable")

    client.app.dependency_overrides[get_embedder] = FailingEmbedder  # type: ignore[attr-defined]
    response = client.post("/api/v1/memory/search", json={"query": "wifi"}, headers=auth)
    assert response.status_code == 502


class FixedReranker:
    """Returns a fixed logit per stored question and records each call."""

    def __init__(self, logits: dict[str, float]) -> None:
        self.logits = logits
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        return [self.logits[document.split("\n")[0]] for document in documents]


LOGITS = {KNOWLEDGE[0][0]: 3.0, KNOWLEDGE[1][0]: 0.5, KNOWLEDGE[2][0]: -4.0}


def _retrieve(
    settings: Settings, reranker: Reranker, text: str, top_k: int = 3
) -> list[tuple[str, float]]:
    async def _run() -> list[tuple[str, float]]:
        engine = create_engine(settings.database_url)
        async with create_sessionmaker(engine)() as session:
            hits = await memory.retrieve(session, FakeEmbedder(), reranker, text, 20, top_k, 0.5)
        await engine.dispose()
        return [(knowledge.question, score) for knowledge, score in hits]

    return asyncio.run(_run())


def test_retrieve_keeps_entries_above_threshold_sorted_by_score(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    for question, answer in KNOWLEDGE:
        _add(client, auth, question, answer)

    hits = _retrieve(settings, FixedReranker(LOGITS), "wifi eduroam")

    assert [question for question, _ in hits] == [KNOWLEDGE[0][0], KNOWLEDGE[1][0]]
    assert hits[0][1] == pytest.approx(0.9526, abs=1e-4)
    assert hits[1][1] == pytest.approx(0.6225, abs=1e-4)


def test_retrieve_handles_extreme_logits(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    for question, answer in KNOWLEDGE:
        _add(client, auth, question, answer)
    logits = {KNOWLEDGE[0][0]: 800.0, KNOWLEDGE[1][0]: -800.0, KNOWLEDGE[2][0]: -math.inf}

    hits = _retrieve(settings, FixedReranker(logits), "wifi eduroam")

    assert hits == [(KNOWLEDGE[0][0], 1.0)]


def test_retrieve_caps_at_top_k(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    for question, answer in KNOWLEDGE:
        _add(client, auth, question, answer)

    hits = _retrieve(settings, FixedReranker(LOGITS), "wifi eduroam", top_k=1)

    assert [question for question, _ in hits] == [KNOWLEDGE[0][0]]


def test_retrieve_reranks_question_and_answer(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    for question, answer in KNOWLEDGE:
        _add(client, auth, question, answer)
    reranker = FixedReranker(LOGITS)

    _retrieve(settings, reranker, "wifi eduroam")

    [(query, documents)] = reranker.calls
    assert query == "wifi eduroam"
    assert sorted(documents) == sorted(f"{question}\n{answer}" for question, answer in KNOWLEDGE)


def test_retrieve_ignores_inactive_knowledge(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    archived = _add(client, auth, *KNOWLEDGE[0])
    _add(client, auth, *KNOWLEDGE[1])
    run_sql(settings, f"UPDATE knowledge SET status = 'archived' WHERE id = '{archived['id']}'")

    hits = _retrieve(settings, FixedReranker(LOGITS), "wifi eduroam")

    assert [question for question, _ in hits] == [KNOWLEDGE[1][0]]


def test_search_does_not_load_embeddings(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _add(client, auth, *KNOWLEDGE[0])

    async def _run() -> set[str]:
        engine = create_engine(settings.database_url)
        async with create_sessionmaker(engine)() as session:
            [(knowledge, _)] = await memory.search(session, FakeEmbedder(), "wifi eduroam", 5)
        await engine.dispose()
        return inspect(knowledge).unloaded

    assert "embedding" in asyncio.run(_run())


def test_retrieve_on_empty_memory_skips_reranker(client: TestClient, settings: Settings) -> None:
    class UnreachableReranker:
        async def rerank(self, query: str, documents: list[str]) -> list[float]:
            raise AssertionError("must not be called")

    assert _retrieve(settings, UnreachableReranker(), "wifi eduroam") == []
