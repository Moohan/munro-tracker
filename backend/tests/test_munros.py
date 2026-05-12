"""Tests for the munros route."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db

@pytest.fixture
def override_db() -> MagicMock:
    """Provide a MagicMock DB and manage FastAPI dependency overrides."""
    mock_db = MagicMock()

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield mock_db
    finally:
        app.dependency_overrides.pop(get_db, None)

@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI TestClient for testing endpoints."""
    return TestClient(app)

def test_list_munros_success(client: TestClient, override_db: MagicMock) -> None:
    """Test list_munros returns a list of munros."""
    mock_row_1 = MagicMock()
    mock_row_1._mapping = {
        "id": 1,
        "name": "Ben Nevis",
        "height_metres": 1345,
        "latitude": 56.796652,
        "longitude": -5.003525,
        "is_bagged": False,
    }
    mock_row_2 = MagicMock()
    mock_row_2._mapping = {
        "id": 2,
        "name": "Ben Macdui",
        "height_metres": 1309,
        "latitude": 57.070635,
        "longitude": -3.669035,
        "is_bagged": True,
    }
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row_1, mock_row_2]
    override_db.execute.return_value = mock_result

    response = client.get("/api/v1/munros")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["name"] == "Ben Nevis"
    assert data[0]["is_bagged"] is False
    assert data[1]["name"] == "Ben Macdui"
    assert data[1]["is_bagged"] is True

def test_list_munros_empty(client: TestClient, override_db: MagicMock) -> None:
    """Test list_munros returns empty list when no munros exist."""
    mock_result = MagicMock()
    mock_result.all.return_value = []
    override_db.execute.return_value = mock_result

    response = client.get("/api/v1/munros")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0

def test_list_munros_with_limit(client: TestClient, override_db: MagicMock) -> None:
    """Test list_munros respects the limit parameter."""
    mock_result = MagicMock()
    mock_result.all.return_value = []
    override_db.execute.return_value = mock_result

    response = client.get("/api/v1/munros?limit=10")
    assert response.status_code == 200

def test_list_munros_invalid_limit(client: TestClient) -> None:
    """Test list_munros rejects invalid limit values."""
    # Limit below minimum
    response = client.get("/api/v1/munros?limit=0")
    assert response.status_code == 422

    # Limit above maximum
    response = client.get("/api/v1/munros?limit=1001")
    assert response.status_code == 422

def test_list_munros_with_user_id(client: TestClient, override_db: MagicMock) -> None:
    """Test list_munros with user_id parameter."""
    user_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    override_db.execute.return_value = mock_result

    response = client.get(f"/api/v1/munros?user_id={user_id}")
    assert response.status_code == 200
