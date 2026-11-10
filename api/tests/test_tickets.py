import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import (
    get_chat_model,
    get_embedder,
    get_glpi,
    get_metrics,
    get_reranker,
    get_web_search,
)
from app.core.config import Settings
from app.db.session import create_engine
from app.services.glpi import Author, GlpiFollowup
from app.services.metrics import CsvMetricsWriter
from app.services.websearch import WebResult
from tests.conftest import (
    TICKET,
    FailingChatModel,
    FailingEmbedder,
    FailingReranker,
    FakeGlpi,
    FakeWebSearch,
    ScriptedChatModel,
    csv_rows,
    override,
    run_sql,
)

URL = f"/api/v1/tickets/{TICKET.id}/answer/stream"
HANDOVER = "Votre demande est transmise à un technicien."


def _recorded_followups(settings: Settings) -> list[int]:
    async def run() -> list[int]:
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT glpi_followup_id FROM ticket_answer")
                )
                return list(result.scalars())
        finally:
            await engine.dispose()

    return asyncio.run(run())


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


def test_ticket_answer_reads_the_followups_as_the_thread(
    client: TestClient, auth: dict[str, str]
) -> None:
    followups = (
        GlpiFollowup(1, Author.BOT, "Supprimez le profil"),
        GlpiFollowup(2, Author.REQUESTER, "Toujours pas"),
    )
    glpi = FakeGlpi(replace(TICKET, followups=followups))
    model = ScriptedChatModel([json.dumps({"action": "answer", "answer": "Réinstallez-le"})])
    override(client, get_glpi, glpi)
    override(client, get_chat_model, model)

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    prompt = model.calls[0][0][1]["content"]
    assert "Support : Supprimez le profil\nUtilisateur : Toujours pas" in prompt
    assert glpi.followups == [(TICKET.id, "Réinstallez-le", False)]


def test_ticket_answer_after_the_support_returns_409(
    client: TestClient, auth: dict[str, str]
) -> None:
    glpi = FakeGlpi(replace(TICKET, followups=(GlpiFollowup(1, Author.BOT, "Supprimez-le"),)))
    override(client, get_glpi, glpi)

    response = client.post(URL, headers=auth)

    assert response.status_code == 409
    assert glpi.followups == []


def _add_knowledge(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/memory/knowledge",
        json={"question": "Eduroam ne marche pas", "answer": "Réinstallez le profil"},
        headers=auth,
    )
    assert response.status_code == 201, response.text


def test_ticket_followup_drops_refs_and_lists_cited_web_pages(
    client: TestClient, auth: dict[str, str]
) -> None:
    _add_knowledge(client, auth)
    glpi = FakeGlpi()
    answer = "Supprimez le profil [K1] [W1], puis reconnectez-vous [W1, W2]."
    model = ScriptedChatModel(
        [
            json.dumps({"action": "search", "query": "eduroam mac"}),
            json.dumps({"action": "search", "query": "profil eduroam"}),
            json.dumps({"action": "answer", "answer": answer, "sources": ["K1", "W1", "W2"]}),
        ]
    )
    # Both searches return the same page: W1 and W2 are one source for the requester.
    page = WebResult("Configurer eduroam", "https://cat.eduroam.org", None, "Profil")
    override(client, get_glpi, glpi)
    override(client, get_chat_model, model)
    override(client, get_web_search, FakeWebSearch([page]))

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    # The API keeps the refs: they map the answer to `sources`.
    assert _events(response.text)[0][1]["answer"] == answer
    assert glpi.followups == [
        (
            TICKET.id,
            "Supprimez le profil, puis reconnectez-vous.\n\n"
            "Sources :\n- Configurer eduroam : https://cat.eduroam.org",
            False,
        )
    ]


def test_ticket_followup_from_memory_only_has_no_sources(
    client: TestClient, auth: dict[str, str]
) -> None:
    _add_knowledge(client, auth)
    glpi = FakeGlpi()
    answer = json.dumps({"action": "answer", "answer": "Réinstallez le profil. [K1]"})
    override(client, get_glpi, glpi)
    override(client, get_chat_model, ScriptedChatModel([answer]))

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    assert glpi.followups == [(TICKET.id, "Réinstallez le profil.", False)]


def test_ticket_handover_posts_the_message_without_sources(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    glpi = FakeGlpi()
    handover = json.dumps(
        {"action": "escalate", "answer": "Transmis à un technicien. [K1]", "reason": "hors sujet"}
    )
    override(client, get_glpi, glpi)
    override(client, get_chat_model, ScriptedChatModel([handover]))

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    [(answer_event, result), done] = _events(response.text)
    assert answer_event == "answer"
    assert (result["kind"], result["message"], result["reason"]) == (
        "escalation",
        "Transmis à un technicien. [K1]",
        "hors sujet",
    )
    assert done == ("done", {"followup_id": 7})
    # The refs mean nothing to the requester, and a handover cites no page to list.
    assert glpi.followups == [(TICKET.id, "Transmis à un technicien.", False)]
    # The handover is recorded: the bot must not answer this ticket again.
    assert _recorded_followups(settings) == [7]


def test_ticket_stream_posts_the_handover(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    glpi = FakeGlpi()
    escalation = {"action": "escalate", "answer": HANDOVER, "reason": "hors sujet"}
    override(client, get_glpi, glpi)
    override(client, get_chat_model, ScriptedChatModel([json.dumps(escalation)]))

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    [(event, result), done] = _events(response.text)
    assert (event, result["kind"], result["message"]) == ("answer", "escalation", HANDOVER)
    assert done == ("done", {"followup_id": 7})
    assert glpi.followups == [(TICKET.id, HANDOVER, False)]
    assert _recorded_followups(settings) == [7]


def test_ticket_stream_after_a_handover_returns_409(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    run_sql(
        settings,
        "INSERT INTO ticket_answer (glpi_ticket_id, glpi_followup_id, knowledge_ids, escalated) "
        f"VALUES ({TICKET.id}, 7, '{{}}', true)",
    )
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)
    override(client, get_chat_model, FailingChatModel())

    response = client.post(URL, headers=auth)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Nothing to answer: the support side wrote last or the ticket was handed to a technician"
    )
    assert glpi.followups == []


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
    _add_knowledge(client, auth)
    override(client, get_glpi, FakeGlpi())
    override(client, dependency, failing)

    response = client.post(URL, headers=auth)

    assert response.status_code == 502
    assert response.json() == {"detail": detail}


def test_ticket_answer_writes_metrics(
    client: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    override(client, get_glpi, FakeGlpi())
    override(client, get_metrics, CsvMetricsWriter(tmp_path))

    assert client.post(URL, headers=auth).status_code == 200

    [run] = csv_rows(tmp_path / "runs.v1.csv")
    assert (run["source"], run["ticket_id"]) == ("ticket_stream", "42")


def test_ticket_answer_records_the_posted_followup(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    override(client, get_glpi, FakeGlpi())

    response = client.post(URL, headers=auth)

    assert response.status_code == 200, response.text
    assert _recorded_followups(settings) == [7]


def test_ticket_answer_records_nothing_when_the_followup_fails(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    override(client, get_glpi, FakeGlpi(fail_followup=True))

    client.post(URL, headers=auth)

    assert _recorded_followups(settings) == []
