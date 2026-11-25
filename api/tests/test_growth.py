import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_embedder, get_glpi
from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.main import create_app
from app.services import growth, survey
from app.services.glpi import GlpiSurvey
from tests.conftest import TICKET, FailingEmbedder, FakeEmbedder, FakeGlpi, override, run_sql
from tests.test_glpi_worker import NEW_TICKET, _process, _queue

URL = f"/api/v1/tickets/{TICKET.id}/verdict"
QUESTION = "Le wifi eduroam ne marche pas sur mon Mac"
ANSWER = "Supprimez le profil eduroam puis reconnectez-vous."


def _answer(settings: Settings, followup_id: int, *knowledge_ids: str) -> None:
    ids = ",".join(knowledge_ids)
    run_sql(
        settings,
        "INSERT INTO ticket_answer "
        "(glpi_ticket_id, glpi_followup_id, knowledge_ids, question, answer) "
        f"VALUES ({TICKET.id}, {followup_id}, '{{{ids}}}', '{QUESTION}', '{ANSWER}')",
    )


def _rows(settings: Settings, query: str) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                return [tuple(row) for row in (await connection.execute(text(query))).all()]
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _learnt(settings: Settings) -> list[Any]:
    return _rows(
        settings,
        "SELECT question, answer, source, glpi_ticket_id FROM knowledge WHERE source = 'llm'",
    )


def _index(settings: Settings, dedup_similarity: float = 1.0) -> list[uuid.UUID]:
    async def run() -> list[uuid.UUID]:
        engine = create_engine(settings.database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await growth.index_validated(session, FakeEmbedder(), dedup_similarity)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_resolved_answer_from_the_web_enters_the_memory(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    override(client, get_glpi, FakeGlpi())

    assert client.post(URL, headers=auth, json={"verdict": "resolved"}).status_code == 204

    assert _learnt(settings) == [(QUESTION, ANSWER, "llm", TICKET.id)]
    [(indexed,)] = _rows(settings, "SELECT indexed_knowledge_id::text FROM ticket_answer")
    hits = client.post("/api/v1/memory/search", json={"query": QUESTION}, headers=auth).json()
    assert hits[0]["knowledge"]["id"] == indexed


def test_resolved_answer_that_cited_the_memory_adds_nothing(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    entry = client.post(
        "/api/v1/memory/knowledge", json={"question": "Wifi", "answer": "R"}, headers=auth
    ).json()["id"]
    _answer(settings, 7, entry)
    override(client, get_glpi, FakeGlpi())

    assert client.post(URL, headers=auth, json={"verdict": "resolved"}).status_code == 204

    assert _learnt(settings) == []


def test_technician_verdict_adds_nothing(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    override(client, get_glpi, FakeGlpi())

    assert client.post(URL, headers=auth, json={"verdict": "technician"}).status_code == 204

    assert _learnt(settings) == []


def test_answer_resolved_while_embeddings_are_down_is_indexed_later(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    override(client, get_glpi, FakeGlpi())
    override(client, get_embedder, FailingEmbedder())

    assert client.post(URL, headers=auth, json={"verdict": "resolved"}).status_code == 204
    assert _learnt(settings) == []

    assert len(_index(settings)) == 1
    assert _learnt(settings) == [(QUESTION, ANSWER, "llm", TICKET.id)]


def test_answer_is_indexed_once(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    override(client, get_glpi, FakeGlpi())
    client.post(URL, headers=auth, json={"verdict": "resolved"})

    assert _index(settings) == []
    assert len(_learnt(settings)) == 1


def test_growth_can_be_turned_off(client: TestClient, settings: Settings) -> None:
    _answer(settings, 7)
    app = create_app(settings.model_copy(update={"memory_growth": False}))
    app.dependency_overrides[get_embedder] = FakeEmbedder
    app.dependency_overrides[get_glpi] = FakeGlpi
    with TestClient(app) as other:
        response = other.post(
            URL,
            headers={"X-API-Key": settings.api_key.get_secret_value()},
            json={"verdict": "resolved"},
        )

    assert response.status_code == 204, response.text
    assert _learnt(settings) == []


def _sync(settings: Settings, score: float) -> None:
    async def run() -> None:
        engine = create_engine(settings.database_url)
        try:
            async with create_sessionmaker(engine)() as session:
                glpi = FakeGlpi(surveys=[GlpiSurvey(TICKET.id, score, None)])
                await survey.sync_surveys(session, glpi, None, 0.8)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_good_survey_validates_the_latest_answer(client: TestClient, settings: Settings) -> None:
    _answer(settings, 7)
    _answer(settings, 9)

    _sync(settings, 0.8)

    assert _rows(settings, "SELECT glpi_followup_id, validated FROM ticket_answer ORDER BY id") == [
        (7, False),
        (9, True),
    ]
    assert len(_index(settings)) == 1


def test_middling_survey_validates_nothing(client: TestClient, settings: Settings) -> None:
    _answer(settings, 7)

    _sync(settings, 0.6)

    assert _index(settings) == []


def test_worker_records_the_question_and_the_followup_text(
    client: TestClient, settings: Settings
) -> None:
    _queue(client, NEW_TICKET)

    assert _process(settings, FakeGlpi())

    [(question, answer)] = _rows(settings, "SELECT question, answer FROM ticket_answer")
    assert question == f"{TICKET.title}\n\n{TICKET.content}"
    assert answer == "Supprimez le profil"


def _validate_all(settings: Settings) -> None:
    run_sql(settings, "UPDATE ticket_answer SET validated = true")


def _entry(client: TestClient, auth: dict[str, str], question: str) -> str:
    return client.post(
        "/api/v1/memory/knowledge", json={"question": question, "answer": "R"}, headers=auth
    ).json()["id"]


def _links(settings: Settings) -> list[Any]:
    return _rows(settings, "SELECT indexed_knowledge_id::text FROM ticket_answer ORDER BY id")


def test_same_problem_solved_twice_gives_one_entry(client: TestClient, settings: Settings) -> None:
    _answer(settings, 7)
    _answer(settings, 9)
    _validate_all(settings)

    [created] = _index(settings, dedup_similarity=0.9)

    assert len(_learnt(settings)) == 1
    assert _links(settings) == [(str(created),), (str(created),)]


def test_answer_close_to_an_active_entry_is_linked_to_it(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    existing = _entry(client, auth, QUESTION)
    _answer(settings, 7)
    _validate_all(settings)

    assert _index(settings, dedup_similarity=0.9) == []

    assert _learnt(settings) == []
    assert _links(settings) == [(existing,)]


def test_answer_far_from_every_entry_is_indexed(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _entry(client, auth, "Imprimante du bâtiment B en panne")
    _answer(settings, 7)
    _validate_all(settings)

    assert len(_index(settings, dedup_similarity=0.9)) == 1
    assert len(_learnt(settings)) == 1


def test_entry_under_review_does_not_block_a_validated_answer(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    flagged = _entry(client, auth, QUESTION)
    run_sql(settings, f"UPDATE knowledge SET status = 'to_review' WHERE id = '{flagged}'")
    _answer(settings, 7)
    _validate_all(settings)

    [created] = _index(settings, dedup_similarity=0.9)

    assert str(created) != flagged
    assert _learnt(settings) == [(QUESTION, ANSWER, "llm", TICKET.id)]


def test_resolved_verdict_dedupes_with_the_configured_similarity(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    existing = _entry(client, auth, QUESTION)
    _answer(settings, 7)
    override(client, get_glpi, FakeGlpi())

    assert client.post(URL, headers=auth, json={"verdict": "resolved"}).status_code == 204

    assert _learnt(settings) == []
    assert _links(settings) == [(existing,)]
