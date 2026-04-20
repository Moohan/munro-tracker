"""Tests for the munros route."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.munro import MunroSummary


@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI TestClient for testing endpoints."""
    return TestClient(app)


def test_list_munros_success(client: TestClient) -> None:
    """Test list_munros returns a list of munros."""
    with patch("app.api.routes.munros.get_db") as mock_get_db:
        # Mock database response
        mock_db = MagicMock()
        mock_row_1 = MagicMock()
        mock_row_1._mapping = {
            "id": 1,
            "name": "Ben Nevis",
            "height_metres": 1345,
            "latitude": 56.796652,
            "longitude": -5.003525,
        }
        mock_row_2 = MagicMock()
        mock_row_2._mapping = {
            "id": 2,
            "name": "Ben Macdui",
            "height_metres": 1309,
            "latitude": 57.070635,
            "longitude": -3.669035,
        }
        mock_db.execute.return_value.all.return_value = [mock_row_1, mock_row_2]
        mock_get_db.return_value = mock_db

        response = client.get("/api/v1/munros")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "Ben Nevis"
        assert data[1]["name"] == "Ben Macdui"


def test_list_munros_empty(client: TestClient) -> None:
    """Test list_munros returns empty list when no munros exist."""
    with patch("app.api.routes.munros.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = []
        mock_get_db.return_value = mock_db

        response = client.get("/api/v1/munros")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0


def test_list_munros_with_limit(client: TestClient) -> None:
    """Test list_munros respects the limit parameter."""
    with patch("app.api.routes.munros.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = []
        mock_get_db.return_value = mock_db

        response = client.get("/api/v1/munros?limit=10")
        assert response.status_code == 200


def test_list_munros_invalid_limit(client: TestClient) -> None:
    """Test list_munros rejects invalid limit values."""
    with patch("app.api.routes.munros.get_db"):
        # Limit below minimum
        response = client.get("/api/v1/munros?limit=0")
        assert response.status_code == 422

        # Limit above maximum
        response = client.get("/api/v1/munros?limit=1000")
        assert response.status_code == 422
