"""Tests for dashboard server."""

from fastapi.testclient import TestClient
from printopt.dashboard.server import create_app


def test_dashboard_index():
    app = create_app()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "printopt" in response.text.lower()


def test_dashboard_api_status():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "plugins" in data
    assert "printer" in data
