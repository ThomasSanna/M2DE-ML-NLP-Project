import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.services.glpi_worker import MAX_ATTEMPTS, GlpiEventWorker
from app.services.llm import ChatModel
from tests.conftest import (
    TICKET,
    FailingChatModel,
    FakeEmbedder,
    FakeGlpi,
    FakeReranker,
    ScriptedChatModel,
    glpi_headers,
    run_sql,
)

NEW_TICKET = {"event": "new", "item": {"id": TICKET.id, "name": TICKET.title}}


def _queue(client: TestClient, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/ingest/ticket", content=body, headers=glpi_headers(body))
    assert response.status_code == 202, response.text


def _process(settings: Settings, glpi: FakeGlpi, model: ChatModel | None = None) -> bool:
    model = model or ScriptedChatModel(
        [json.dumps({"action": "answer", "answer": "Supprimez le profil [K1]"})]
    )

    async def run() -> bool:
        engine = create_engine(settings.database_url)
        worker = GlpiEventWorker(
            create_sessionmaker(engine), FakeEmbedder(), FakeReranker(), model, None, glpi, settings
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


@pytest.mark.parametrize(
    ("payload", "status", "error"),
    [
        ({"event": "update", "item": {"id": TICKET.id}}, "done", None),
        ({"event": "new", "item": {"id": 999}}, "failed", "GLPI ticket not found"),
        ({"event": "new", "item": {"name": "Sans id"}}, "failed", "No ticket id in the payload"),
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
