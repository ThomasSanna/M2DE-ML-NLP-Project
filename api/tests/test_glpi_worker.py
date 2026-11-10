import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.services.glpi import Author, GlpiError, GlpiFollowup
from app.services.glpi_worker import MAX_ATTEMPTS, GlpiEventWorker
from app.services.llm import ChatModel
from app.services.metrics import CsvMetricsWriter
from tests.conftest import (
    TICKET,
    FailingChatModel,
    FakeEmbedder,
    FakeGlpi,
    FakeReranker,
    ScriptedChatModel,
    csv_rows,
    glpi_headers,
    run_sql,
)

NEW_TICKET = {"event": "new", "item": {"id": TICKET.id, "name": TICKET.title}}
BOT_ANSWER = GlpiFollowup(1, Author.BOT, "Supprimez le profil")
REPLY = GlpiFollowup(2, Author.REQUESTER, "Toujours pas")


def _new_followup(followup: GlpiFollowup) -> dict[str, object]:
    item = {"id": followup.id, "itemtype": "Ticket", "items_id": TICKET.id}
    return {"event": "new", "item": item, "parent_item": {"id": TICKET.id}}


def _queue(client: TestClient, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/ingest/ticket", content=body, headers=glpi_headers(body))
    assert response.status_code == 202, response.text


def _process(
    settings: Settings,
    glpi: FakeGlpi,
    model: ChatModel | None = None,
    metrics: CsvMetricsWriter | None = None,
) -> bool:
    model = model or ScriptedChatModel(
        [json.dumps({"action": "answer", "answer": "Supprimez le profil [K1]"})]
    )

    async def run() -> bool:
        engine = create_engine(settings.database_url)
        worker = GlpiEventWorker(
            create_sessionmaker(engine),
            FakeEmbedder(),
            FakeReranker(),
            model,
            None,
            metrics,
            glpi,
            settings,
        )
        try:
            return await worker.process_next()
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _event_state(settings: Settings) -> tuple[str, int, str | None]:
    async def run() -> tuple[str, int, str | None]:
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT status, attempts, last_error FROM glpi_event")
                )
                status, attempts, error = result.one()
        finally:
            await engine.dispose()
        return status, attempts, error

    return asyncio.run(run())


def test_new_ticket_is_answered_once_in_a_followup(client: TestClient, settings: Settings) -> None:
    glpi = FakeGlpi()
    _queue(client, NEW_TICKET)

    assert _process(settings, glpi)
    assert not _process(settings, glpi)

    assert glpi.followups == [(TICKET.id, "Supprimez le profil", False)]
    assert _event_state(settings) == ("done", 1, None)


def test_failed_event_is_retried_after_the_delay(client: TestClient, settings: Settings) -> None:
    glpi = FakeGlpi()
    _queue(client, NEW_TICKET)

    assert _process(settings, glpi, FailingChatModel())
    assert _event_state(settings) == ("failed", 1, "boom")
    assert not _process(settings, glpi)

    run_sql(settings, "UPDATE glpi_event SET processed_at = now() - interval '1 hour'")
    assert _process(settings, glpi)

    assert glpi.followups == [(TICKET.id, "Supprimez le profil", False)]
    assert _event_state(settings) == ("done", 2, None)


def test_failed_event_is_dropped_after_max_attempts(client: TestClient, settings: Settings) -> None:
    _queue(client, NEW_TICKET)
    run_sql(
        settings,
        f"UPDATE glpi_event SET status = 'failed', attempts = {MAX_ATTEMPTS}, "
        "processed_at = now() - interval '1 hour'",
    )
    assert not _process(settings, FakeGlpi())


def test_requester_followup_is_answered_with_the_thread(
    client: TestClient, settings: Settings
) -> None:
    glpi = FakeGlpi(replace(TICKET, followups=(BOT_ANSWER, REPLY)))
    model = ScriptedChatModel([json.dumps({"action": "answer", "answer": "Réinstallez-le"})])
    _queue(client, _new_followup(REPLY))

    assert _process(settings, glpi, model)

    prompt = model.calls[0][0][1]["content"]
    assert "Support : Supprimez le profil\nUtilisateur : Toujours pas" in prompt
    assert glpi.followups == [(TICKET.id, "Réinstallez-le", False)]
    assert _event_state(settings) == ("done", 1, None)


@pytest.mark.parametrize(
    ("followups", "payload"),
    [
        # The bot's own followup: GLPI sends the webhook for it too.
        ((BOT_ANSWER,), _new_followup(BOT_ANSWER)),
        # A technician took over.
        (
            (
                REPLY,
                GlpiFollowup(3, Author.OTHER, "Je regarde"),
                GlpiFollowup(4, Author.REQUESTER, "Merci"),
            ),
            _new_followup(GlpiFollowup(4, Author.REQUESTER, "Merci")),
        ),
        # A newer followup: its own event answers the thread.
        ((REPLY, GlpiFollowup(3, Author.REQUESTER, "Sous Windows")), _new_followup(REPLY)),
        ((REPLY,), NEW_TICKET),
        # Private, or deleted before the worker read the ticket.
        ((), _new_followup(REPLY)),
    ],
)
def test_followup_the_bot_should_not_answer_posts_nothing(
    client: TestClient,
    settings: Settings,
    followups: tuple[GlpiFollowup, ...],
    payload: dict[str, object],
) -> None:
    glpi = FakeGlpi(replace(TICKET, followups=followups))
    _queue(client, payload)

    assert _process(settings, glpi, FailingChatModel())

    assert glpi.followups == []
    assert _event_state(settings) == ("done", 1, None)


@pytest.mark.parametrize(
    ("payload", "status", "error"),
    [
        ({"event": "update", "item": {"id": TICKET.id}}, "done", None),
        ({"event": "new", "item": {"id": 999}}, "failed", "GLPI ticket not found"),
        ({"event": "new", "item": {"name": "Sans id"}}, "failed", "No ticket id in the payload"),
        (
            {"event": "new", "item": {"id": 5, "itemtype": "Change", "items_id": TICKET.id}},
            "failed",
            "No ticket id in the payload",
        ),
    ],
)
def test_event_without_a_ticket_to_answer_posts_nothing(
    client: TestClient,
    settings: Settings,
    payload: dict[str, object],
    status: str,
    error: str | None,
) -> None:
    glpi = FakeGlpi()
    _queue(client, payload)

    assert _process(settings, glpi)

    assert glpi.followups == []
    assert _event_state(settings) == (status, 1, error)


def test_worker_writes_metrics_for_the_ticket(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    _queue(client, NEW_TICKET)

    assert _process(settings, FakeGlpi(), metrics=CsvMetricsWriter(tmp_path))

    [run] = csv_rows(tmp_path / "runs.v1.csv")
    assert (run["source"], run["ticket_id"], run["outcome"]) == ("worker", "42", "answered")


def _ticket_answers(settings: Settings) -> list[tuple[int, int, list[object]]]:
    async def run() -> list[tuple[int, int, list[object]]]:
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        "SELECT glpi_ticket_id, glpi_followup_id, knowledge_ids FROM ticket_answer"
                    )
                )
                return [tuple(row) for row in result.all()]
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_worker_records_the_entries_its_followup_cited(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = client.post(
        "/api/v1/memory/knowledge",
        json={"question": "Wifi eduroam sur Mac", "answer": "Supprimez le profil"},
        headers=auth,
    ).json()["id"]
    _queue(client, NEW_TICKET)

    assert _process(settings, FakeGlpi())

    [(ticket_id, followup_id, knowledge_ids)] = _ticket_answers(settings)
    assert (ticket_id, followup_id) == (TICKET.id, 7)
    assert [str(knowledge_id) for knowledge_id in knowledge_ids] == [entry]


HANDOVER = "Votre demande est transmise à un technicien."


def test_handed_over_ticket_is_never_answered_again(client: TestClient, settings: Settings) -> None:
    glpi = FakeGlpi()
    escalation = {"action": "escalate", "answer": HANDOVER, "reason": "hors sujet"}
    _queue(client, NEW_TICKET)

    assert _process(settings, glpi, ScriptedChatModel([json.dumps(escalation)]))

    # The reason goes to the technicians in a private followup, never to the requester.
    assert glpi.followups == [
        (TICKET.id, HANDOVER, False),
        (TICKET.id, "Passation à un technicien. Raison : hors sujet", True),
    ]
    assert _ticket_answers(settings) == [(TICKET.id, 7, [])]

    run_sql(settings, "DELETE FROM glpi_event")
    reply = GlpiFollowup(8, Author.REQUESTER, "Et alors ?")
    later = FakeGlpi(replace(TICKET, followups=(GlpiFollowup(7, Author.BOT, HANDOVER), reply)))
    _queue(client, _new_followup(reply))

    # A model call would fail the event: the handover alone keeps the bot silent.
    assert _process(settings, later, FailingChatModel())

    assert later.followups == []
    assert _event_state(settings) == ("done", 1, None)


def test_handover_without_reason_posts_no_private_note(
    client: TestClient, settings: Settings
) -> None:
    glpi = FakeGlpi()
    _queue(client, NEW_TICKET)

    escalation = {"action": "escalate", "answer": HANDOVER, "reason": " "}
    assert _process(settings, glpi, ScriptedChatModel([json.dumps(escalation)]))

    assert glpi.followups == [(TICKET.id, HANDOVER, False)]


class _NoPrivateGlpi(FakeGlpi):
    async def add_followup(self, ticket_id: int, text: str, *, private: bool) -> int:
        if private:
            raise GlpiError("GLPI down")
        return await super().add_followup(ticket_id, text, private=private)


def test_failed_handover_note_does_not_fail_the_event(
    client: TestClient, settings: Settings
) -> None:
    glpi = _NoPrivateGlpi()
    _queue(client, NEW_TICKET)

    escalation = {"action": "escalate", "answer": HANDOVER, "reason": "hors sujet"}
    assert _process(settings, glpi, ScriptedChatModel([json.dumps(escalation)]))

    assert glpi.followups == [(TICKET.id, HANDOVER, False)]
    assert _event_state(settings) == ("done", 1, None)
    assert _ticket_answers(settings) == [(TICKET.id, 7, [])]


def test_worker_records_nothing_when_the_followup_fails(
    client: TestClient, settings: Settings
) -> None:
    _queue(client, NEW_TICKET)

    assert _process(settings, FakeGlpi(fail_followup=True))

    assert _ticket_answers(settings) == []
