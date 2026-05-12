from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.redis_client import get_redis
from app.infrastructure.session import get_db
from app.main import app
from app.services.health import check_health


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def override_db(mock_db: MagicMock):
    def _override():
        yield mock_db

    return _override


def test_healthcheck_all_systems_ok(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.execute.side_effect = [
        MagicMock(scalar_one=MagicMock(return_value=1)),
        MagicMock(scalar_one=MagicMock(return_value="PostGIS 3.4")),
    ]
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    app.dependency_overrides[get_db] = override_db(mock_db)
    app.dependency_overrides[get_redis] = lambda: mock_redis
    try:
        response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "redis": "ok",
        "postgis": "PostGIS 3.4",
    }


def test_healthcheck_returns_degraded_when_database_fails(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("database unavailable")
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    app.dependency_overrides[get_db] = override_db(mock_db)
    app.dependency_overrides[get_redis] = lambda: mock_redis
    try:
        response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "down"
    assert response.json()["redis"] == "ok"


def test_healthcheck_returns_degraded_when_redis_fails(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.execute.side_effect = [
        MagicMock(scalar_one=MagicMock(return_value=1)),
        MagicMock(scalar_one=MagicMock(return_value="PostGIS 3.4")),
    ]
    mock_redis = MagicMock()
    mock_redis.ping.side_effect = RuntimeError("redis unavailable")

    app.dependency_overrides[get_db] = override_db(mock_db)
    app.dependency_overrides[get_redis] = lambda: mock_redis
    try:
        response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "ok"
    assert response.json()["redis"] == "down"


def test_check_health_service_handles_failures() -> None:
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("db")
    mock_redis = MagicMock()
    mock_redis.ping.side_effect = Exception("redis")

    result = check_health(mock_db, mock_redis)

    assert result["status"] == "degraded"
    assert result["database"] == "down"
    assert result["redis"] == "down"
    assert result["postgis"] is None
