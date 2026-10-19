import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from tests.conftest import WEBHOOK_SECRET

PAYLOAD = {"event": "new", "item": {"id": 42, "name": "Problème wifi"}}


def _glpi_headers(body: bytes, timestamp: str = "1760000000") -> dict[str, str]:
    """En-têtes tels que GLPI 11 les calcule : hmac_sha256(body . timestamp, secret)."""
    signature = hmac.new(WEBHOOK_SECRET.encode(), body + timestamp.encode(), hashlib.sha256)
    return {
        "Content-Type": "application/json",
        "X-GLPI-signature": signature.hexdigest(),
        "X-GLPI-timestamp": timestamp,
    }


def test_signed_webhook_is_queued(client: TestClient) -> None:
    body = json.dumps(PAYLOAD).encode()
    response = client.post("/api/v1/ingest/ticket", content=body, headers=_glpi_headers(body))
    assert response.status_code == 202
    assert response.json() == {"event_id": 1}


def test_bad_signature_is_rejected(client: TestClient) -> None:
    body = json.dumps(PAYLOAD).encode()
    headers = _glpi_headers(body) | {"X-GLPI-signature": "0" * 64}
    assert client.post("/api/v1/ingest/ticket", content=body, headers=headers).status_code == 401


def test_tampered_body_is_rejected(client: TestClient) -> None:
    headers = _glpi_headers(json.dumps(PAYLOAD).encode())
    tampered = json.dumps(PAYLOAD | {"event": "delete"}).encode()
    response = client.post("/api/v1/ingest/ticket", content=tampered, headers=headers)
    assert response.status_code == 401


def test_missing_signature_is_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/ingest/ticket", json=PAYLOAD).status_code == 401


def test_non_object_body_is_rejected(client: TestClient) -> None:
    body = b"[1, 2, 3]"
    response = client.post("/api/v1/ingest/ticket", content=body, headers=_glpi_headers(body))
    assert response.status_code == 422
