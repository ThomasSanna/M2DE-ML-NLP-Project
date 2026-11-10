import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.schemas.answer import (
    AnswerResult,
    EscalationResult,
    KnowledgeCitation,
    Usage,
    WebCitation,
)
from app.services import survey
from app.services.glpi import GlpiSurvey
from tests.conftest import FakeGlpi

TICKET_ID = 42


def _knowledge(client: TestClient, auth: dict[str, str], question: str) -> uuid.UUID:
    response = client.post(
        "/api/v1/memory/knowledge",
        json={"question": question, "answer": "Réponse"},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _result(*knowledge_ids: uuid.UUID) -> AnswerResult:
    sources = [
        KnowledgeCitation(ref=f"K{i}", knowledge_id=knowledge_id, question="Q")
        for i, knowledge_id in enumerate(knowledge_ids, start=1)
    ]
    return AnswerResult(
        answer="Réponse",
        sources=[*sources, WebCitation(ref="W1", url="https://example.org", title="Page")],
        memory=[],
        searches=[],
        steps=1,
        usage=Usage(prompt_tokens=1, completion_tokens=1),
    )


def _escalation() -> EscalationResult:
    return EscalationResult(
        message="Transmis.",
        reason="hors sujet",
        memory=[],
        searches=[],
        steps=1,
        usage=Usage(prompt_tokens=1, completion_tokens=1),
    )


def _record(
    settings: Settings, ticket_id: int, followup_id: int, result: AnswerResult | EscalationResult
) -> None:
    async def run() -> None:
        engine = create_engine(settings.database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                await survey.record_answer(session, ticket_id, followup_id, result)
        finally:
            await engine.dispose()

    asyncio.run(run())


def _sync(settings: Settings, glpi: FakeGlpi) -> int:
    async def run() -> int:
        engine = create_engine(settings.database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await survey.sync_surveys(session, glpi)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _rows(settings: Settings, query: str) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(text(query))).all())
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _feedback(settings: Settings) -> list[Any]:
    return _rows(
        settings,
        "SELECT knowledge_id, source, score, comment, glpi_ticket_id FROM feedback "
        "ORDER BY knowledge_id",
    )


def test_record_answer_keeps_only_cited_memory_entries(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")

    _record(settings, TICKET_ID, 7, _result(entry))

    assert _rows(
        settings, "SELECT glpi_ticket_id, glpi_followup_id, knowledge_ids FROM ticket_answer"
    ) == [(TICKET_ID, 7, [entry])]


def test_record_answer_marks_a_handover(client: TestClient, settings: Settings) -> None:
    _record(settings, TICKET_ID, 7, _escalation())

    assert _rows(
        settings,
        "SELECT glpi_ticket_id, glpi_followup_id, knowledge_ids, escalated FROM ticket_answer",
    ) == [(TICKET_ID, 7, [], True)]


def test_survey_credits_nothing_for_a_handover(client: TestClient, settings: Settings) -> None:
    _record(settings, TICKET_ID, 7, _escalation())

    _sync(settings, FakeGlpi(surveys=[GlpiSurvey(TICKET_ID, 0.8, "Clair")]))

    assert _feedback(settings) == []


def test_survey_scores_every_entry_cited_on_the_ticket(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    first = _knowledge(client, auth, "Wifi eduroam")
    second = _knowledge(client, auth, "Mot de passe ENT")
    _record(settings, TICKET_ID, 7, _result(first))
    _record(settings, TICKET_ID, 8, _result(first, second))
    glpi = FakeGlpi(surveys=[GlpiSurvey(TICKET_ID, 0.8, "Clair")])

    assert _sync(settings, glpi) == 2

    assert _feedback(settings) == sorted(
        [
            (first, "survey", 0.8, "Clair", TICKET_ID),
            (second, "survey", 0.8, "Clair", TICKET_ID),
        ]
    )


def test_survey_sync_writes_once_then_follows_edits(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _record(settings, TICKET_ID, 7, _result(entry))
    glpi = FakeGlpi(surveys=[GlpiSurvey(TICKET_ID, 0.8, "Clair")])
    assert _sync(settings, glpi) == 1

    assert _sync(settings, glpi) == 0
    glpi.surveys = [GlpiSurvey(TICKET_ID, 0.2, None)]
    assert _sync(settings, glpi) == 1

    assert _feedback(settings) == [(entry, "survey", 0.2, None, TICKET_ID)]


def test_survey_of_a_ticket_the_bot_did_not_answer_is_ignored(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _record(settings, TICKET_ID, 7, _result(entry))

    assert _sync(settings, FakeGlpi(surveys=[GlpiSurvey(99, 1.0, None)])) == 0
    assert _feedback(settings) == []


def test_survey_skips_entries_deleted_since_the_answer(
    client: TestClient, settings: Settings
) -> None:
    _record(settings, TICKET_ID, 7, _result(uuid.uuid4()))

    assert _sync(settings, FakeGlpi(surveys=[GlpiSurvey(TICKET_ID, 1.0, None)])) == 0
    assert _feedback(settings) == []


def test_answer_from_the_web_only_gets_no_feedback(client: TestClient, settings: Settings) -> None:
    _record(settings, TICKET_ID, 7, _result())

    assert _sync(settings, FakeGlpi(surveys=[GlpiSurvey(TICKET_ID, 1.0, None)])) == 0
    assert _feedback(settings) == []
