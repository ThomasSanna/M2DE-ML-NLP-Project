import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_chat_model, get_embedder, get_reranker, get_web_search
from app.core.config import Settings
from app.db.session import create_engine
from app.services.agent_prompt import NO_MEMORY
from app.services.answer import RETRIEVAL_QUERY_MAX_CHARS
from app.services.embeddings import EmbeddingError
from app.services.llm import ChatMessage, Completion, LLMError
from app.services.reranker import RerankerError
from app.services.websearch import WebResult
from tests.conftest import FakeEmbedder, FakeWebSearch, ScriptedChatModel

QUESTION = "Je n'arrive plus à me connecter au wifi eduroam"
ANSWER = "Réinstallez le profil eduroam via CAT."
THREAD = [{"role": "user", "content": "Le wifi eduroam ne marche pas sur mon Mac"}]


def _add_knowledge(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/memory/knowledge", json={"question": QUESTION, "answer": ANSWER}, headers=auth
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _override(client: TestClient, dependency: object, value: object) -> None:
    # TestClient.app is typed as ASGIApp, not FastAPI.
    client.app.dependency_overrides[dependency] = lambda: value  # type: ignore[attr-defined]


def test_answer_requires_api_key(client: TestClient) -> None:
    assert client.post("/api/v1/answer", json={"messages": THREAD}).status_code == 401


def test_answer_returns_cited_sources(client: TestClient, auth: dict[str, str]) -> None:
    knowledge_id = _add_knowledge(client, auth)
    model = ScriptedChatModel(
        [
            json.dumps({"action": "search", "query": "eduroam mac"}),
            json.dumps(
                {
                    "action": "answer",
                    "answer": "Supprimez le profil [K1] [W1]",
                    "sources": ["K1", "W1"],
                }
            ),
        ]
    )
    web = FakeWebSearch(
        [WebResult("Configurer eduroam", "https://cat.eduroam.org", None, "Profil")]
    )
    _override(client, get_chat_model, model)
    _override(client, get_web_search, web)

    response = client.post("/api/v1/answer", json={"messages": THREAD}, headers=auth)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "kind": "answer",
        "answer": "Supprimez le profil [K1] [W1]",
        "sources": [
            {"ref": "K1", "type": "knowledge", "knowledge_id": knowledge_id, "question": QUESTION},
            {
                "ref": "W1",
                "type": "web",
                "url": "https://cat.eduroam.org",
                "title": "Configurer eduroam",
            },
        ],
        "memory": [{"knowledge_id": knowledge_id, "score": pytest.approx(0.9933, abs=1e-4)}],
        "searches": ["eduroam mac"],
        "steps": 2,
        "usage": {"prompt_tokens": 200, "completion_tokens": 20},
    }
    first_user_message = model.calls[0][0][1]["content"]
    assert f"[K1] Q : {QUESTION}" in first_user_message
    assert "Utilisateur : Le wifi eduroam" in first_user_message


def test_answer_without_memory_says_so(client: TestClient, auth: dict[str, str]) -> None:
    model = ScriptedChatModel([json.dumps({"action": "answer", "answer": "Je ne sais pas"})])
    _override(client, get_chat_model, model)

    response = client.post("/api/v1/answer", json={"messages": THREAD}, headers=auth)

    assert response.status_code == 200, response.text
    assert response.json()["memory"] == []
    assert response.json()["sources"] == []
    assert NO_MEMORY in model.calls[0][0][1]["content"]


class RecordingReranker:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.queries.append(query)
        return [5.0] * len(documents)


def test_answer_retrieves_on_last_user_message(client: TestClient, auth: dict[str, str]) -> None:
    reranker = RecordingReranker()
    _add_knowledge(client, auth)
    _override(client, get_reranker, reranker)
    thread = [
        {"role": "user", "content": "imprimante"},
        {"role": "assistant", "content": "Laquelle ?"},
        {"role": "user", "content": "wifi eduroam"},
    ]

    response = client.post("/api/v1/answer", json={"messages": thread}, headers=auth)

    assert response.status_code == 200, response.text
    assert reranker.queries == ["wifi eduroam"]


def test_answer_caps_the_retrieval_query(client: TestClient, auth: dict[str, str]) -> None:
    class RecordingEmbedder(FakeEmbedder):
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.texts.extend(texts)
            return await super().embed(texts)

    _add_knowledge(client, auth)
    embedder = RecordingEmbedder()
    reranker = RecordingReranker()
    _override(client, get_embedder, embedder)
    _override(client, get_reranker, reranker)
    pasted_log = "wifi eduroam " + "x" * 10_000

    response = client.post(
        "/api/v1/answer", json={"messages": [{"role": "user", "content": pasted_log}]}, headers=auth
    )

    assert response.status_code == 200, response.text
    assert embedder.texts == [pasted_log[:RETRIEVAL_QUERY_MAX_CHARS]]
    assert reranker.queries == [pasted_log[:RETRIEVAL_QUERY_MAX_CHARS]]


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "user", "content": "wifi"}, {"role": "assistant", "content": "Lequel ?"}],
        [{"role": "system", "content": "wifi"}],
        [{"role": "user", "content": "   "}],
        [{"role": "user", "content": "wifi"}] * 51,
    ],
)
def test_answer_validates_thread(
    client: TestClient, auth: dict[str, str], messages: list[dict[str, str]]
) -> None:
    response = client.post("/api/v1/answer", json={"messages": messages}, headers=auth)
    assert response.status_code == 422


class FailingEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("embeddings down")


class FailingReranker:
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RerankerError("reranker down")


class FailingChatModel:
    async def complete(self, messages: list[ChatMessage], schema: dict[str, object]) -> Completion:
        raise LLMError("boom")


@pytest.mark.parametrize(
    ("dependency", "failing", "detail"),
    [
        (get_embedder, FailingEmbedder(), "Embeddings server unavailable"),
        (get_reranker, FailingReranker(), "Reranker unavailable"),
        (get_chat_model, FailingChatModel(), "Language model unavailable"),
    ],
)
def test_answer_upstream_failures_return_502(
    client: TestClient, auth: dict[str, str], dependency: object, failing: object, detail: str
) -> None:
    _add_knowledge(client, auth)
    _override(client, dependency, failing)

    response = client.post("/api/v1/answer", json={"messages": THREAD}, headers=auth)

    assert response.status_code == 502
    assert response.json() == {"detail": detail}


def test_answer_survives_web_search_failure(client: TestClient, auth: dict[str, str]) -> None:
    model = ScriptedChatModel(
        [
            json.dumps({"action": "search", "query": "eduroam mac"}),
            json.dumps({"action": "answer", "answer": "Avec la mémoire"}),
        ]
    )
    _override(client, get_chat_model, model)
    _override(client, get_web_search, FakeWebSearch(fail=True))

    response = client.post("/api/v1/answer", json={"messages": THREAD}, headers=auth)

    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Avec la mémoire"


def test_answer_releases_db_transaction_before_the_model(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    class ProbingChatModel:
        idle_in_transaction = -1

        async def complete(
            self, messages: list[ChatMessage], schema: dict[str, object]
        ) -> Completion:
            engine = create_engine(settings.database_url)
            async with engine.connect() as connection:
                self.idle_in_transaction = await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'"
                    )
                )
            await engine.dispose()
            return Completion(json.dumps({"action": "answer", "answer": "ok"}), "stop", 1, 1)

    model = ProbingChatModel()
    _add_knowledge(client, auth)
    _override(client, get_chat_model, model)

    response = client.post("/api/v1/answer", json={"messages": THREAD}, headers=auth)

    assert response.status_code == 200, response.text
    assert model.idle_in_transaction == 0
