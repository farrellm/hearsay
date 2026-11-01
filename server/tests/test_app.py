from fastapi.testclient import TestClient

from hearsay import __version__
from hearsay.db import SCHEMA_VERSION


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "schema": SCHEMA_VERSION}


def test_unknown_api_route_is_404_json(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_shell_is_served_and_revalidated(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Hearsay</title>" in response.text
    assert response.headers["cache-control"] == "no-cache"
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cacheable(client: TestClient) -> None:
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "cache-control" not in response.headers


def test_deep_link_cold_start_gets_the_shell(client: TestClient) -> None:
    response = client.get("/e/12?t=30.5")
    assert response.status_code == 200
    assert "<title>Hearsay</title>" in response.text


def test_missing_file_is_a_real_404(client: TestClient) -> None:
    assert client.get("/assets/missing-123.js").status_code == 404
    assert client.get("/icon-999.png").status_code == 404
