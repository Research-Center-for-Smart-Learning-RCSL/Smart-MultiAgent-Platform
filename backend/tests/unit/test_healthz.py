from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_request_id_echoed(client: TestClient) -> None:
    r = client.get("/healthz", headers={"X-Request-Id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


def test_request_id_generated_when_absent(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.headers["x-request-id"]
