import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_glpi
from app.core.config import Settings
from tests.conftest import TICKET, FakeGlpi, override, run_sql

URL = f"/api/v1/tickets/{TICKET.id}/verdict"
RESOLVED_NOTE = (
    "Le demandeur indique que la réponse de l'assistant a résolu son problème : le ticket peut"
    " être clos."
)
TECHNICIAN_NOTE = (
    "Le demandeur demande l'intervention d'un technicien : l'assistant ne répondra plus sur ce"
    " ticket."
)


def _answer(settings: Settings, followup_id: int, *, escalated: bool = False) -> None:
    run_sql(
        settings,
        "INSERT INTO ticket_answer (glpi_ticket_id, glpi_followup_id, knowledge_ids, escalated) "
        f"VALUES ({TICKET.id}, {followup_id}, '{{}}', {str(escalated).lower()})",
    )


def _verdicts(client: TestClient, auth: dict[str, str]) -> list[object]:
    answers = client.get(f"/api/v1/tickets/{TICKET.id}/answers", headers=auth).json()
    return [answer["verdict"] for answer in answers]


def test_resolved_adds_a_private_note_and_solves_the_ticket(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)

    response = client.post(URL, headers=auth, json={"verdict": "resolved"})

    assert response.status_code == 204, response.text
    assert glpi.followups == [(TICKET.id, RESOLVED_NOTE, True)]
    assert glpi.solved == [TICKET.id]
    assert _verdicts(client, auth) == ["resolved"]


def test_technician_hands_the_ticket_over(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)

    response = client.post(URL, headers=auth, json={"verdict": "technician"})

    assert response.status_code == 204, response.text
    assert glpi.followups == [(TICKET.id, TECHNICIAN_NOTE, True)]
    assert glpi.solved == []
    # The bot no longer answers the ticket, even when the requester writes last.
    override(client, get_glpi, FakeGlpi())
    stream = client.post(f"/api/v1/tickets/{TICKET.id}/answer/stream", headers=auth)
    assert stream.status_code == 409, stream.text


def test_verdict_applies_to_the_latest_answer_only(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    _answer(settings, 9)
    override(client, get_glpi, FakeGlpi())

    assert client.post(URL, headers=auth, json={"verdict": "resolved"}).status_code == 204

    assert _verdicts(client, auth) == [None, "resolved"]


def test_verdict_without_an_answer_returns_409(client: TestClient, auth: dict[str, str]) -> None:
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)

    response = client.post(URL, headers=auth, json={"verdict": "resolved"})

    assert response.status_code == 409, response.text
    assert glpi.followups == []


def test_verdict_on_a_handover_returns_409(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7, escalated=True)
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)

    response = client.post(URL, headers=auth, json={"verdict": "technician"})

    assert response.status_code == 409, response.text
    assert glpi.followups == []


def test_second_verdict_returns_409(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7)
    glpi = FakeGlpi()
    override(client, get_glpi, glpi)
    client.post(URL, headers=auth, json={"verdict": "resolved"})

    response = client.post(URL, headers=auth, json={"verdict": "technician"})

    assert response.status_code == 409, response.text
    assert glpi.followups == [(TICKET.id, RESOLVED_NOTE, True)]
    assert _verdicts(client, auth) == ["resolved"]


@pytest.mark.parametrize("glpi", [FakeGlpi(fail_followup=True), FakeGlpi(fail_solve=True)])
def test_glpi_failure_returns_502_and_withdraws_the_verdict(
    client: TestClient, auth: dict[str, str], settings: Settings, glpi: FakeGlpi
) -> None:
    _answer(settings, 7)
    override(client, get_glpi, glpi)

    response = client.post(URL, headers=auth, json={"verdict": "resolved"})

    assert response.status_code == 502, response.text
    assert response.json() == {"detail": "GLPI unavailable"}
    assert _verdicts(client, auth) == [None]


def test_verdict_requires_api_key(client: TestClient) -> None:
    override(client, get_glpi, FakeGlpi())
    assert client.post(URL, json={"verdict": "resolved"}).status_code == 401


@pytest.mark.parametrize(
    ("ticket_id", "body"),
    [("0", {"verdict": "resolved"}), ("abc", {"verdict": "resolved"}), ("42", {"verdict": "x"})],
)
def test_verdict_rejects_invalid_input(
    client: TestClient, auth: dict[str, str], ticket_id: str, body: dict[str, str]
) -> None:
    override(client, get_glpi, FakeGlpi())
    response = client.post(f"/api/v1/tickets/{ticket_id}/verdict", headers=auth, json=body)
    assert response.status_code == 422


def test_verdict_without_glpi_token_returns_503(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post(URL, headers=auth, json={"verdict": "resolved"}).status_code == 503


def test_answers_report_a_handover(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    _answer(settings, 7, escalated=True)

    body = client.get(f"/api/v1/tickets/{TICKET.id}/answers", headers=auth).json()

    assert body == [{"followup_id": 7, "sources": [], "escalated": True, "verdict": None}]
