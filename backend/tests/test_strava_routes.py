from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.session import get_db
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def override_db(mock_db: MagicMock):
    def _override():
        yield mock_db

    return _override


def test_strava_login_redirects_to_authorisation_url(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")

    with patch(
        "app.api.routes.strava.build_authorization_url",
        return_value="https://www.strava.com/oauth/authorize?state=token",
    ):
        response = client.get(
            "/api/v1/strava/oauth/login",
            params={"next_url": "/dashboard"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://www.strava.com/oauth/authorize")


def test_strava_oauth_status_reports_missing_client_secret(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "replace-me")

    response = client.get("/api/v1/strava/oauth/status")

    assert response.status_code == 200
    assert response.json()["oauth_available"] is False
    assert response.json()["reason"] == "missing_client_secret"


def test_strava_login_redirects_back_to_frontend_when_oauth_is_not_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "replace-me")

    response = client.get(
        "/api/v1/strava/oauth/login",
        params={"next_url": "/"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:5173/")
    assert "status=error" in response.headers["location"]
    assert "error_code=missing_client_secret" in response.headers["location"]


def test_strava_login_returns_json_when_oauth_is_not_configured_without_next_url(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "replace-me")

    response = client.get("/api/v1/strava/oauth/login", follow_redirects=False)

    assert response.status_code == 400
    assert "STRAVA_CLIENT_SECRET" in response.json()["detail"]


def test_strava_callback_redirects_back_to_frontend(client: TestClient) -> None:
    payload = {
        "user_id": uuid.uuid4(),
        "strava_athlete_id": 99,
        "display_name": "Test Athlete",
        "accepted_scopes": ["activity:read_all", "read"],
        "sync_enqueued": True,
        "sync_task_id": "task-123",
        "redirect_to": "http://localhost:5173/dashboard",
    }

    with patch(
        "app.api.routes.strava.onboard_user_from_strava_callback",
        return_value=payload,
    ):
        response = client.get(
            "/api/v1/strava/oauth/callback",
            params={"code": "abc", "state": "signed-state", "scope": "read,activity:read_all"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "status=connected" in response.headers["location"]
    assert f"user_id={payload['user_id']}" in response.headers["location"]


def test_strava_callback_redirects_error_back_to_frontend_when_state_is_trusted(
    client: TestClient,
) -> None:
    with patch(
        "app.api.routes.strava.parse_oauth_state",
        return_value={"next_url": "http://localhost:5173/"},
    ):
        response = client.get(
            "/api/v1/strava/oauth/callback",
            params={"error": "access_denied", "state": "signed-state"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:5173/")
    assert "status=error" in response.headers["location"]
    assert "error_code=authorisation_failed" in response.headers["location"]


def test_enqueue_strava_sync_returns_accepted(client: TestClient) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.get.return_value = SimpleNamespace(id=user_id)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.sync_latest_activities_for_user.delay",
        return_value=SimpleNamespace(id="task-456"),
    ):
        try:
            response = client.post(f"/api/v1/strava/users/{user_id}/sync")
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["task_id"] == "task-456"


def test_verify_strava_webhook_subscription(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "verify-me")

    response = client.get(
        "/api/v1/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "challenge-123",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"hub.challenge": "challenge-123"}


def test_receive_strava_webhook_event_enqueues_activity_sync(client: TestClient) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.scalar.return_value = SimpleNamespace(id=user_id, strava_athlete_id=321)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.sync_activity_for_user.delay",
        return_value=SimpleNamespace(id="task-789"),
    ):
        try:
            response = client.post(
                "/api/v1/strava/webhook",
                json={
                    "owner_id": 321,
                    "object_id": 555,
                    "object_type": "activity",
                    "aspect_type": "create",
                    "event_time": 1715529600,
                    "subscription_id": 1,
                    "updates": {},
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {"status": "enqueued", "task_id": "task-789"}
