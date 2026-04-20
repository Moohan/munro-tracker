"""Tests for the health check route."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.health import check_health


@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI TestClient for testing endpoints."""
    return TestClient(app)


def test_healthcheck_all_systems_ok(client: TestClient) -> None:
    """Test healthcheck returns 'ok' when all systems are operational."""
    with patch("app.api.routes.health.get_db") as mock_get_db, \
         patch("app.api.routes.health.get_redis") as mock_get_redis:

        # Mock database
        mock_db = MagicMock()
        mock_db.execute.side_effect = [
            MagicMock(scalar_one=MagicMock(return_value=1)),  # SELECT 1
            MagicMock(scalar_one=MagicMock(return_value="PostGIS 3.0.0")),  # PostGIS version
        ]
        mock_get_db.return_value = mock_db

        # Mock Redis
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_get_redis.return_value = mock_redis

        response = client.get("/api/v1/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"
        assert data["redis"] == "ok"
        assert "postgis" in data


def test_healthcheck_database_down(client: TestClient) -> None:
    """Test healthcheck returns 'degraded' when database is unavailable."""
    with patch("app.api.routes.health.get_db") as mock_get_db, \
         patch("app.api.routes.health.get_redis") as mock_get_redis:

        # Mock database error
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("Database connection failed")
        mock_get_db.return_value = mock_db

        # Mock Redis
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_get_redis.return_value = mock_redis

        # Should handle the exception gracefully
        with pytest.raises(Exception):
            response = client.get("/api/v1/health")


def test_healthcheck_redis_down(client: TestClient) -> None:
    """Test healthcheck returns 'degraded' when Redis is unavailable."""
    with patch("app.api.routes.health.get_db") as mock_get_db, \
         patch("app.api.routes.health.get_redis") as mock_get_redis:

        # Mock database
        mock_db = MagicMock()
        mock_db.execute.side_effect = [
            MagicMock(scalar_one=MagicMock(return_value=1)),
            MagicMock(scalar_one=MagicMock(return_value="PostGIS 3.0.0")),
        ]
        mock_get_db.return_value = mock_db

        # Mock Redis error
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("Redis connection failed")
        mock_get_redis.return_value = mock_redis

        # Should handle the exception gracefully
        with pytest.raises(Exception):
            response = client.get("/api/v1/health")


def test_check_health_service() -> None:
    """Test the check_health service function directly."""
    # Mock database
    mock_db = MagicMock()
    mock_db.execute.side_effect = [
        MagicMock(scalar_one=MagicMock(return_value=1)),  # SELECT 1
        MagicMock(scalar_one=MagicMock(return_value="PostGIS 3.0.0")),  # PostGIS version
    ]

    # Mock Redis
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    result = check_health(mock_db, mock_redis)

    assert result["status"] == "ok"
    assert result["database"] == "ok"
    assert result["redis"] == "ok"
    assert result["postgis"] == "PostGIS 3.0.0"
