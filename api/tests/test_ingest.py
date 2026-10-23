import json

from fastapi.testclient import TestClient

from tests.conftest import glpi_headers

PAYLOAD = {"event": "new", "item": {"id": 42, "name": "Problème wifi"}}


def test_signed_webhook_is_queued(client: TestClient) -> None:
    body = json.dumps(PAYLOAD).encode()
    response = client.post("/api/v1/ingest/ticket", content=body, headers=glpi_headers(body))
    assert response.status_code == 202
    assert response.json() == {"event_id": 1}


def test_bad_signature_is_rejected(client: TestClient) -> None:
    body = json.dumps(PAYLOAD).encode()
    headers = glpi_headers(body) | {"X-GLPI-signature": "0" * 64}
    assert client.post("/api/v1/ingest/ticket", content=body, headers=headers).status_code == 401


def test_tampered_body_is_rejected(client: TestClient) -> None:
    headers = glpi_headers(json.dumps(PAYLOAD).encode())
    tampered = json.dumps(PAYLOAD | {"event": "delete"}).encode()
    response = client.post("/api/v1/ingest/ticket", content=tampered, headers=headers)
    assert response.status_code == 401


def test_missing_signature_is_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/ingest/ticket", json=PAYLOAD).status_code == 401


def test_non_object_body_is_rejected(client: TestClient) -> None:
    body = b"[1, 2, 3]"
    response = client.post("/api/v1/ingest/ticket", content=body, headers=glpi_headers(body))
    assert response.status_code == 422
