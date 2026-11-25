import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_embedder
from app.core.config import Settings
from tests.conftest import FailingEmbedder, override, run_sql

QUESTION = "Le wifi eduroam ne marche pas"


def _under_review(client: TestClient, auth: dict[str, str], settings: Settings) -> str:
    entry = client.post(
        "/api/v1/memory/knowledge", json={"question": QUESTION, "answer": "Faux"}, headers=auth
    ).json()["id"]
    run_sql(settings, f"UPDATE knowledge SET status = 'to_review' WHERE id = '{entry}'")
    for _ in range(3):
        run_sql(
            settings,
            f"INSERT INTO feedback (knowledge_id, source, score) VALUES ('{entry}', 'user', 0)",
        )
    return entry


def _review(client: TestClient, auth: dict[str, str], entry: str, body: dict) -> object:
    return client.post(f"/api/v1/memory/knowledge/{entry}/review", json=body, headers=auth)


def _status(client: TestClient, auth: dict[str, str], entry: str) -> str:
    return client.get(f"/api/v1/memory/knowledge/{entry}", headers=auth).json()["status"]


def test_review_list_shows_entries_with_their_feedback(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    assert client.get("/api/v1/memory/review", headers=auth).json() == []
    entry = _under_review(client, auth, settings)

    [item] = client.get("/api/v1/memory/review", headers=auth).json()

    assert item["knowledge"]["id"] == entry
    # (0.5 * 2 + 0) / (2 + 3) = 0.2
    assert item["feedback"] == pytest.approx(0.2)
    assert item["ratings"] == 3


def test_reactivate_puts_the_entry_back_in_the_memory(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _under_review(client, auth, settings)

    response = _review(client, auth, entry, {"action": "reactivate"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    hits = client.post("/api/v1/memory/search", json={"query": QUESTION}, headers=auth).json()
    assert [hit["knowledge"]["id"] for hit in hits] == [entry]
    assert client.get("/api/v1/memory/review", headers=auth).json() == []


def test_archive_takes_the_entry_out_for_good(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _under_review(client, auth, settings)

    response = _review(client, auth, entry, {"action": "archive"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"


def test_correct_replaces_the_entry_with_a_fresh_one(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _under_review(client, auth, settings)

    response = _review(
        client, auth, entry, {"action": "correct", "answer": "Supprimez le profil eduroam."}
    )

    assert response.status_code == 200, response.text
    fixed = response.json()
    assert fixed["id"] != entry
    assert (fixed["question"], fixed["answer"]) == (QUESTION, "Supprimez le profil eduroam.")
    assert (fixed["source"], fixed["status"], fixed["reuse_count"]) == ("technician", "active", 0)
    assert _status(client, auth, entry) == "archived"
    hits = client.post("/api/v1/memory/search", json={"query": QUESTION}, headers=auth).json()
    assert [hit["knowledge"]["id"] for hit in hits] == [fixed["id"]]


def test_review_of_an_entry_not_under_review_returns_409(
    client: TestClient, auth: dict[str, str]
) -> None:
    entry = client.post(
        "/api/v1/memory/knowledge", json={"question": QUESTION, "answer": "R"}, headers=auth
    ).json()["id"]

    response = _review(client, auth, entry, {"action": "archive"})

    assert response.status_code == 409, response.text
    assert _status(client, auth, entry) == "active"


def test_review_of_an_unknown_entry_returns_404(client: TestClient, auth: dict[str, str]) -> None:
    assert _review(client, auth, str(uuid.uuid4()), {"action": "archive"}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"action": "correct"},
        {"action": "correct", "answer": ""},
        {"action": "reactivate", "answer": "Autre réponse"},
        {"action": "delete"},
    ],
)
def test_review_rejects_invalid_input(
    client: TestClient, auth: dict[str, str], settings: Settings, body: dict
) -> None:
    entry = _under_review(client, auth, settings)

    assert _review(client, auth, entry, body).status_code == 422
    assert _status(client, auth, entry) == "to_review"


def test_correct_with_embeddings_down_returns_502_and_changes_nothing(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _under_review(client, auth, settings)
    override(client, get_embedder, FailingEmbedder())

    response = _review(client, auth, entry, {"action": "correct", "answer": "Correct."})

    assert response.status_code == 502, response.text
    assert _status(client, auth, entry) == "to_review"


def test_review_routes_require_api_key(client: TestClient) -> None:
    assert client.get("/api/v1/memory/review").status_code == 401
    response = client.post(
        f"/api/v1/memory/knowledge/{uuid.uuid4()}/review", json={"action": "archive"}
    )
    assert response.status_code == 401
