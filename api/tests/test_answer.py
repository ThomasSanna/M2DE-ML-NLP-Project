import json
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model, get_glpi
from app.services.glpi import GlpiError, GlpiTicket
from app.services.llm import LLMError

TICKET = GlpiTicket(id=42, title="Wifi eduroam", content="Je n'arrive plus à me connecter au wifi")
URL = "/api/v1/tickets/42/answer/stream"


class FakeChatModel:
    def __init__(self, tokens: list[str], fail_after: int | None = None) -> None:
        self.tokens = tokens
        self.fail_after = fail_after
        self.messages: list[dict[str, str]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.messages = messages
        for i, token in enumerate(self.tokens):
            if i == self.fail_after:
                raise LLMError("coupure")
            yield token


class FakeGlpi:
    def __init__(self, fail_followup: bool = False) -> None:
        self.tickets = {TICKET.id: TICKET}
        self.followups: list[tuple[int, str, bool]] = []
        self.fail_followup = fail_followup

    async def get_ticket(self, ticket_id: int) -> GlpiTicket | None:
        return self.tickets.get(ticket_id)

    async def add_followup(self, ticket_id: int, text: str, *, private: bool) -> int:
        if self.fail_followup:
            raise GlpiError("GLPI HS")
        self.followups.append((ticket_id, text, private))
        return 7


@pytest.fixture
def glpi(client: TestClient) -> Iterator[FakeGlpi]:
    fake = FakeGlpi()
    client.app.dependency_overrides[get_glpi] = lambda: fake
    yield fake


def _use_llm(client: TestClient, llm: FakeChatModel) -> FakeChatModel:
    client.app.dependency_overrides[get_chat_model] = lambda: llm
    return llm


def _events(client: TestClient, auth: dict[str, str], url: str = URL) -> list[tuple[str, object]]:
    with client.stream("GET", url, headers=auth) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.read().decode()
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_requires_api_key(client: TestClient, glpi: FakeGlpi) -> None:
    assert client.get(URL).status_code == 401


def test_unknown_ticket_is_404(client: TestClient, auth: dict[str, str], glpi: FakeGlpi) -> None:
    _use_llm(client, FakeChatModel(["x"]))
    assert client.get("/api/v1/tickets/999/answer/stream", headers=auth).status_code == 404


def test_glpi_not_configured_is_503(client: TestClient, auth: dict[str, str]) -> None:
    # Les réglages de test n'ont pas de GLPI_USER_TOKEN.
    assert client.get(URL, headers=auth).status_code == 503


def test_streams_chunks_then_posts_followup(
    client: TestClient, auth: dict[str, str], glpi: FakeGlpi
) -> None:
    tokens = [f"t{i} " for i in range(13)]
    _use_llm(client, FakeChatModel(tokens))

    events = _events(client, auth)

    names = [name for name, _ in events]
    assert names == ["sources", "token", "token", "token", "done"]
    chunks = [data["text"] for name, data in events if name == "token"]
    assert chunks == ["".join(tokens[0:6]), "".join(tokens[6:12]), tokens[12]]  # 6 par défaut
    assert events[-1] == ("done", {"followup_id": 7})
    assert glpi.followups == [(42, "".join(tokens).strip(), False)]


def test_memory_hits_are_in_prompt_and_sources(
    client: TestClient, auth: dict[str, str], glpi: FakeGlpi
) -> None:
    created = client.post(
        "/api/v1/memory/knowledge",
        json={"question": "Connexion au wifi eduroam impossible", "answer": "Réinstallez CAT."},
        headers=auth,
    ).json()
    llm = _use_llm(client, FakeChatModel(["ok"]))

    events = _events(client, auth)

    name, sources = events[0]
    assert name == "sources"
    assert sources[0]["id"] == created["id"]
    assert "Réinstallez CAT." in llm.messages[-1]["content"]
    assert TICKET.content in llm.messages[-1]["content"]


def test_llm_failure_sends_error_and_no_followup(
    client: TestClient, auth: dict[str, str], glpi: FakeGlpi
) -> None:
    _use_llm(client, FakeChatModel(["a", "b", "c"], fail_after=2))

    events = _events(client, auth)

    assert events[-1][0] == "error"
    assert "done" not in [name for name, _ in events]
    assert glpi.followups == []


def test_empty_answer_is_an_error(client: TestClient, auth: dict[str, str], glpi: FakeGlpi) -> None:
    _use_llm(client, FakeChatModel(["  "]))
    assert _events(client, auth)[-1][0] == "error"
    assert glpi.followups == []


def test_glpi_followup_failure_sends_error(client: TestClient, auth: dict[str, str]) -> None:
    client.app.dependency_overrides[get_glpi] = lambda: FakeGlpi(fail_followup=True)
    _use_llm(client, FakeChatModel(["réponse"]))
    assert _events(client, auth)[-1][0] == "error"
