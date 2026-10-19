import uuid

from fastapi.testclient import TestClient

from app.api.deps import get_embedder
from app.core.config import Settings
from app.services.embeddings import EmbeddingError
from tests.conftest import run_sql

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
    bad = {"X-API-Key": "mauvaise-cle"}
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
            raise EmbeddingError("serveur injoignable")

    client.app.dependency_overrides[get_embedder] = FailingEmbedder  # type: ignore[attr-defined]
    response = client.post("/api/v1/memory/search", json={"query": "wifi"}, headers=auth)
    assert response.status_code == 502
