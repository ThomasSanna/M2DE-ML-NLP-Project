import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model, get_embedder, get_glpi, get_reranker
from app.services.glpi import GlpiError, GlpiTicket
from tests.conftest import (
    FailingChatModel,
    FailingEmbedder,
    FailingReranker,
    ScriptedChatModel,
    override,
)

TICKET = GlpiTicket(
    id=42, title="Wifi eduroam", content="Le wifi eduroam ne marche pas sur mon Mac"
)
URL = f"/api/v1/tickets/{TICKET.id}/answer/stream"


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


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_ticket_answer_requires_api_key(client: TestClient) -> None:
    override(client, get_glpi, FakeGlpi())
    assert client.post(URL).status_code == 401


def test_ticket_answer_streams_answer_then_posts_followup(
    client: TestClient, auth: dict[str, str]
) -> None:
    glpi = FakeGlpi()
    model = ScriptedChatModel([json.dumps({"action": "answer", "answer": "Supprimez le profil"})])
    override(client, get_glpi, glpi)
    override(client, get_chat_model, model)

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    [(answer_event, result), done] = _events(response.text)
    assert answer_event == "answer"
    assert result["answer"] == "Supprimez le profil"
    assert done == ("done", {"followup_id": 7})
    assert glpi.followups == [(TICKET.id, "Supprimez le profil", False)]
    prompt = model.calls[0][0][1]["content"]
    assert TICKET.title in prompt
    assert TICKET.content in prompt


def test_ticket_answer_reports_followup_failure_in_stream(
    client: TestClient, auth: dict[str, str]
) -> None:
    override(client, get_glpi, FakeGlpi(fail_followup=True))

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    assert [event for event, _ in _events(response.text)] == ["answer", "error"]
    assert _events(response.text)[-1] == ("error", {"detail": "GLPI unavailable"})


def test_ticket_answer_unknown_ticket_returns_404(client: TestClient, auth: dict[str, str]) -> None:
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)

    response = client.post("/api/v1/tickets/999/answer/stream", headers=auth)

    assert response.status_code == 404
    assert response.json() == {"detail": "GLPI ticket not found"}
    assert glpi.followups == []


@pytest.mark.parametrize("ticket_id", ["abc", "0", "-1"])
def test_ticket_answer_validates_ticket_id(
    client: TestClient, auth: dict[str, str], ticket_id: str
) -> None:
    override(client, get_glpi, FakeGlpi())
    response = client.post(f"/api/v1/tickets/{ticket_id}/answer/stream", headers=auth)
    assert response.status_code == 422


def test_ticket_answer_without_glpi_token_returns_503(
    client: TestClient, auth: dict[str, str]
) -> None:
    # The test settings have no GLPI_USER_TOKEN, so the app has no GLPI client.
    response = client.post(URL, headers=auth)
    assert response.status_code == 503


@pytest.mark.parametrize(
    ("dependency", "failing", "detail"),
    [
        (get_embedder, FailingEmbedder(), "Embeddings server unavailable"),
        (get_reranker, FailingReranker(), "Reranker unavailable"),
        (get_chat_model, FailingChatModel(), "Language model unavailable"),
        (get_glpi, FakeGlpi(fail_read=True), "GLPI unavailable"),
    ],
)
def test_ticket_answer_upstream_failures_return_502(
    client: TestClient, auth: dict[str, str], dependency: object, failing: object, detail: str
) -> None:
    client.post(
        "/api/v1/memory/knowledge",
        json={"question": "Eduroam ne marche pas", "answer": "Réinstallez le profil"},
        headers=auth,
    )
    override(client, get_glpi, FakeGlpi())
    override(client, dependency, failing)

    response = client.post(URL, headers=auth)

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
