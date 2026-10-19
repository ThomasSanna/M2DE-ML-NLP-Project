from fastapi.testclient import TestClient


def test_live(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_when_database_reachable(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
