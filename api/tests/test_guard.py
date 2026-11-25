import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model, get_embedder, get_guard_model, get_metrics
from app.services.guard import BLOCKED_MESSAGE, BLOCKED_REASON, GUARD_SCHEMA
from app.services.llm import ChatModel
from app.services.metrics import CsvMetricsWriter
from tests.conftest import (
    FailingChatModel,
    FailingEmbedder,
    ScriptedChatModel,
    csv_rows,
    override,
)

ANSWER = {"action": "answer", "answer": "Réinstallez le profil eduroam.", "sources": []}
THREAD = [
    {"role": "user", "content": "Le wifi eduroam ne marche pas sur mon Mac"},
    {"role": "assistant", "content": "Avez-vous réinstallé le profil ?"},
    {"role": "user", "content": "Oui, toujours pareil"},
]
OFF_TOPIC = [{"role": "user", "content": "Donne-moi une recette de figatellu"}]


def _post(client: TestClient, auth: dict[str, str], messages: list[dict[str, str]]) -> dict:
    response = client.post("/api/v1/answer", json={"messages": messages}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_off_topic_request_is_handed_over_before_any_other_call(
    client: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    # Failing embedder and model: any call past the guard would make the request fail.
    override(client, get_guard_model, ScriptedChatModel([json.dumps({"on_topic": False})]))
    override(client, get_embedder, FailingEmbedder())
    override(client, get_chat_model, FailingChatModel())
    override(client, get_metrics, CsvMetricsWriter(tmp_path))

    assert _post(client, auth, OFF_TOPIC) == {
        "kind": "escalation",
        "message": BLOCKED_MESSAGE,
        "reason": BLOCKED_REASON,
        "memory": [],
        "searches": [],
        "steps": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    [run] = csv_rows(tmp_path / "runs.v1.csv")
    assert (run["outcome"], run["steps"], run["retrieval_ms"]) == ("blocked", "0", "")
    [call] = csv_rows(tmp_path / "calls.v1.csv")
    assert (call["kind"], call["status"], call["prompt_tokens"]) == ("guard", "ok", "100")


def test_guard_reads_the_requester_text_only(client: TestClient, auth: dict[str, str]) -> None:
    guard = ScriptedChatModel([json.dumps({"on_topic": True})])
    override(client, get_guard_model, guard)
    override(client, get_chat_model, ScriptedChatModel([json.dumps(ANSWER)]))

    assert _post(client, auth, THREAD)["kind"] == "answer"

    [(messages, schema)] = guard.calls
    assert schema == GUARD_SCHEMA
    request = messages[-1]["content"]
    assert isinstance(request, str)
    assert "Le wifi eduroam ne marche pas" in request
    assert "Oui, toujours pareil" in request
    # The support side's replies are not the requester's request.
    assert "réinstallé" not in request


@pytest.mark.parametrize(
    "guard",
    [
        FailingChatModel(),
        ScriptedChatModel(["pas du JSON"]),
        ScriptedChatModel([json.dumps({"on_topic": "non"})]),
    ],
    ids=["unavailable", "invalid_json", "not_a_boolean"],
)
def test_failing_guard_lets_the_answer_model_decide(
    client: TestClient, auth: dict[str, str], tmp_path: Path, guard: ChatModel
) -> None:
    override(client, get_guard_model, guard)
    override(client, get_chat_model, ScriptedChatModel([json.dumps(ANSWER)]))
    override(client, get_metrics, CsvMetricsWriter(tmp_path))

    assert _post(client, auth, THREAD)["kind"] == "answer"

    [run] = csv_rows(tmp_path / "runs.v1.csv")
    assert run["outcome"] == "answered"
    assert csv_rows(tmp_path / "calls.v1.csv")[0]["kind"] == "guard"
