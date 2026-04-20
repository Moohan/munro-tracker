"""Tests for the main FastAPI application."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI TestClient for testing endpoints."""
    return TestClient(app)


def test_root_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the root endpoint returns service information."""
    monkeypatch.setenv("APP_NAME", "munro-tracker-test")
    monkeypatch.setenv("API_V1_PREFIX", "/api/v1")

    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "docs" in data
    assert "api" in data
    assert data["docs"] == "/docs"


def test_docs_available(client: TestClient) -> None:
    """Test that FastAPI docs are available."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower() or "openapi" in response.text.lower()


def test_redoc_available(client: TestClient) -> None:
    """Test that ReDoc documentation is available."""
    response = client.get("/redoc")
    assert response.status_code == 200
