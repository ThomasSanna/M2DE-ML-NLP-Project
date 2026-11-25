import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_glpi
from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.services import feedback, survey
from app.services.feedback import ReviewPolicy
from app.services.glpi import GlpiSurvey
from tests.conftest import TICKET, FakeGlpi, override, run_sql

POLICY = ReviewPolicy(threshold=0.3, min_ratings=3, prior=2.0, half_life_days=None)


def _knowledge(client: TestClient, auth: dict[str, str], question: str) -> uuid.UUID:
    response = client.post(
        "/api/v1/memory/knowledge", json={"question": question, "answer": "R"}, headers=auth
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _rate(settings: Settings, knowledge_id: uuid.UUID, *scores: float) -> None:
    for score in scores:
        run_sql(
            settings,
            "INSERT INTO feedback (knowledge_id, source, score) "
            f"VALUES ('{knowledge_id}', 'user', {score})",
        )


def _status(settings: Settings, knowledge_id: uuid.UUID) -> str:
    async def run() -> str:
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(f"SELECT status FROM knowledge WHERE id = '{knowledge_id}'")
                )
                return result.scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _flag(settings: Settings, ids: set[uuid.UUID], policy: ReviewPolicy = POLICY) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_engine(settings.database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                flagged = await feedback.flag_for_review(session, ids, policy)
                await session.commit()
                return flagged
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_poorly_rated_entry_goes_to_review(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    # (0.5 * 2 + 0) / (2 + 3) = 0.2, under 0.3.
    _rate(settings, entry, 0.0, 0.0, 0.0)

    assert _flag(settings, {entry}) == [entry]
    assert _status(settings, entry) == "to_review"


def test_too_few_ratings_keep_the_entry_active(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _rate(settings, entry, 0.0, 0.0)

    assert _flag(settings, {entry}) == []
    assert _status(settings, entry) == "active"


def test_mixed_ratings_keep_the_entry_active(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    # (1 + 0 + 0 + 1) / 5 = 0.4
    _rate(settings, entry, 0.0, 0.0, 1.0)

    assert _flag(settings, {entry}) == []


def test_zero_threshold_never_sends_to_review(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _rate(settings, entry, 0.0, 0.0, 0.0, 0.0)

    assert _flag(settings, {entry}, ReviewPolicy(0.0, 3, 2.0, None)) == []


def test_archived_entry_is_not_sent_back_to_review(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    run_sql(settings, f"UPDATE knowledge SET status = 'archived' WHERE id = '{entry}'")
    _rate(settings, entry, 0.0, 0.0, 0.0)

    assert _flag(settings, {entry}) == []
    assert _status(settings, entry) == "archived"


def test_entry_sent_to_review_is_no_longer_retrieved(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam ne marche pas")
    _rate(settings, entry, 0.0, 0.0, 0.0)
    _flag(settings, {entry})

    response = client.post("/api/v1/memory/search", json={"query": "wifi eduroam"}, headers=auth)

    assert response.json() == []


def test_survey_sync_sends_poorly_rated_entries_to_review(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _rate(settings, entry, 0.0, 0.0)
    run_sql(
        settings,
        "INSERT INTO ticket_answer (glpi_ticket_id, glpi_followup_id, knowledge_ids) "
        f"VALUES (42, 7, '{{{entry}}}')",
    )
    glpi = FakeGlpi(surveys=[GlpiSurvey(42, 0.0, None)])

    async def run() -> int:
        engine = create_engine(settings.database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await survey.sync_surveys(session, glpi, POLICY)
        finally:
            await engine.dispose()

    assert asyncio.run(run()) == 1
    assert _status(settings, entry) == "to_review"


def test_technician_verdict_sends_a_poorly_rated_entry_to_review(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _rate(settings, entry, 0.0, 0.0)
    run_sql(
        settings,
        "INSERT INTO ticket_answer (glpi_ticket_id, glpi_followup_id, knowledge_ids) "
        f"VALUES ({TICKET.id}, 7, '{{{entry}}}')",
    )
    override(client, get_glpi, FakeGlpi())

    response = client.post(
        f"/api/v1/tickets/{TICKET.id}/verdict", headers=auth, json={"verdict": "technician"}
    )

    assert response.status_code == 204, response.text
    assert _status(settings, entry) == "to_review"


def test_resolved_verdict_keeps_the_entry_active(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = _knowledge(client, auth, "Wifi eduroam")
    _rate(settings, entry, 0.0, 0.0)
    run_sql(
        settings,
        "INSERT INTO ticket_answer (glpi_ticket_id, glpi_followup_id, knowledge_ids) "
        f"VALUES ({TICKET.id}, 7, '{{{entry}}}')",
    )
    override(client, get_glpi, FakeGlpi())

    client.post(f"/api/v1/tickets/{TICKET.id}/verdict", headers=auth, json={"verdict": "resolved"})

    # (1 + 0 + 0 + 1) / 5 = 0.4
    assert _status(settings, entry) == "active"
