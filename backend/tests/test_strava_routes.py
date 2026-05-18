from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from celery.exceptions import CeleryError
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
        "app.api.routes.strava.get_strava_oauth_status",
        return_value={
            "oauth_available": True,
            "reason": None,
            "message": "Strava OAuth is available.",
        },
    ), patch(
        "app.api.routes.strava.create_oauth_state",
        return_value="signed-state",
    ), patch(
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
) -> None:
    with patch(
        "app.api.routes.strava.get_strava_oauth_status",
        return_value={
            "oauth_available": False,
            "reason": "missing_client_secret",
            "message": "Add STRAVA_CLIENT_SECRET to enable it.",
        },
    ):
        response = client.get("/api/v1/strava/oauth/status")

    assert response.status_code == 200
    assert response.json()["oauth_available"] is False
    assert response.json()["reason"] == "missing_client_secret"


def test_strava_login_redirects_back_to_frontend_when_oauth_is_not_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")

    with patch(
        "app.api.routes.strava.get_strava_oauth_status",
        return_value={
            "oauth_available": False,
            "reason": "missing_client_secret",
            "message": "STRAVA_CLIENT_SECRET is not configured.",
        },
    ):
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
    with patch(
        "app.api.routes.strava.get_strava_oauth_status",
        return_value={
            "oauth_available": False,
            "reason": "missing_client_secret",
            "message": "STRAVA_CLIENT_SECRET is not configured.",
        },
    ):
        response = client.get("/api/v1/strava/oauth/login", follow_redirects=False)

    assert response.status_code == 400
    assert "STRAVA_CLIENT_SECRET" in response.json()["detail"]


def test_update_strava_oauth_settings_returns_available_status(client: TestClient) -> None:
    mock_db = MagicMock()
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch("app.api.routes.strava.save_strava_oauth_settings") as mock_save, patch(
        "app.api.routes.strava.get_strava_oauth_status",
        return_value={
            "oauth_available": True,
            "reason": None,
            "message": "Strava OAuth is available.",
        },
    ):
        try:
            response = client.put(
                "/api/v1/strava/oauth/settings",
                json={"client_id": 123, "client_secret": "secret"},
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    mock_save.assert_called_once()
    mock_db.commit.assert_called_once()
    assert response.json()["oauth_available"] is True


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


def test_strava_callback_redirects_callback_value_error_back_to_frontend(
    client: TestClient,
) -> None:
    with patch(
        "app.api.routes.strava.onboard_user_from_strava_callback",
        side_effect=ValueError("Strava token exchange did not return an athlete id."),
    ), patch(
        "app.api.routes.strava.parse_oauth_state",
        return_value={"next_url": "http://localhost:5173/"},
    ):
        response = client.get(
            "/api/v1/strava/oauth/callback",
            params={"code": "abc", "state": "signed-state", "scope": "read,activity:read_all"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:5173/")
    assert "status=error" in response.headers["location"]
    assert "error_code=athlete_missing" in response.headers["location"]


def test_enqueue_strava_sync_returns_accepted(client: TestClient) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.get.return_value = SimpleNamespace(id=user_id)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.enqueue_latest_activities_sync",
        return_value=SimpleNamespace(id="task-456"),
    ) as mock_enqueue:
        try:
            response = client.post(f"/api/v1/strava/users/{user_id}/sync")
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["task_id"] == "task-456"
    assert response.json()["sync_mode"] == "latest"
    mock_enqueue.assert_called_once_with(user_id)


def test_enqueue_full_strava_sync_returns_accepted(client: TestClient) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.get.return_value = SimpleNamespace(id=user_id)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.enqueue_full_activities_sync",
        return_value=SimpleNamespace(id="task-789"),
    ) as mock_enqueue:
        try:
            response = client.post(f"/api/v1/strava/users/{user_id}/sync?mode=full")
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "user_id": str(user_id),
        "task_id": "task-789",
        "activity_limit": None,
        "sync_mode": "full",
    }
    mock_enqueue.assert_called_once_with(user_id)


def test_enqueue_strava_totals_refresh_returns_accepted(client: TestClient) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.get.return_value = SimpleNamespace(id=user_id)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.enqueue_activity_totals_refresh",
        return_value=SimpleNamespace(id="task-totals"),
    ) as mock_enqueue:
        try:
            response = client.post(f"/api/v1/strava/users/{user_id}/totals/refresh")
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "user_id": str(user_id),
        "task_id": "task-totals",
        "activity_limit": None,
        "sync_mode": "totals",
    }
    mock_enqueue.assert_called_once_with(user_id)


def test_enqueue_strava_sync_returns_service_unavailable_when_enqueue_fails(
    client: TestClient,
) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.get.return_value = SimpleNamespace(id=user_id)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.enqueue_latest_activities_sync",
        side_effect=CeleryError("broker unavailable"),
    ):
        try:
            response = client.post(f"/api/v1/strava/users/{user_id}/sync")
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "Unable to queue a Strava sync right now."


def test_get_strava_task_status_returns_progress_payload(client: TestClient) -> None:
    with patch(
        "app.api.routes.strava.AsyncResult",
        return_value=SimpleNamespace(
            state="PROGRESS",
            result={
                "sync_mode": "full",
                "activities_seen": 12,
                "activities_processed": 5,
                "activities_with_matches": 2,
                "bag_rows_written": 2,
                "cached_strava_activities": 25,
                "total_strava_activities": 220,
                "progress_is_indeterminate": True,
                "progress_percentage": 41.7,
                "sync_phase": "processing",
                "started_at": "2026-05-13T13:00:00+00:00",
                "updated_at": "2026-05-13T13:10:00+00:00",
                "estimated_remaining_seconds": 240,
                "read_window_limit": 100,
                "read_window_usage": 18,
                "read_window_remaining": 82,
                "read_daily_limit": 1000,
                "read_daily_usage": 211,
                "read_daily_remaining": 789,
                "read_window_resets_at": "2026-05-13T13:15:05+00:00",
                "read_daily_resets_at": "2026-05-14T00:00:05+00:00",
                "rate_limit_wait_seconds": None,
                "retry_after": None,
                "message": "Checked 5 activities so far while syncing your full Strava history.",
            },
        ),
    ):
        response = client.get("/api/v1/strava/tasks/task-123")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-123",
        "state": "PROGRESS",
        "message": "Checked 5 activities so far while syncing your full Strava history.",
        "sync_mode": "full",
        "activities_seen": 12,
        "activities_processed": 5,
        "activities_with_matches": 2,
        "activities_skipped_no_polyline": 0,
        "activities_skipped_invalid_polyline": 0,
        "activities_skipped_unverified_elevation": 0,
        "bag_rows_written": 2,
        "cached_strava_activities": 25,
        "total_strava_activities": 220,
        "progress_percentage": 41.7,
        "progress_is_indeterminate": True,
        "sync_phase": "processing",
        "started_at": "2026-05-13T13:00:00+00:00",
        "updated_at": "2026-05-13T13:10:00+00:00",
        "estimated_remaining_seconds": 240,
        "rate_limit_scope": None,
        "read_window_limit": 100,
        "read_window_usage": 18,
        "read_window_remaining": 82,
        "read_daily_limit": 1000,
        "read_daily_usage": 211,
        "read_daily_remaining": 789,
        "read_window_resets_at": "2026-05-13T13:15:05+00:00",
        "read_daily_resets_at": "2026-05-14T00:00:05+00:00",
        "rate_limit_wait_seconds": None,
        "retry_after": None,
        "is_complete": False,
        "is_error": False,
    }


def test_get_strava_task_status_returns_retry_payload_metadata(client: TestClient) -> None:
    with patch(
        "app.api.routes.strava.AsyncResult",
        return_value=SimpleNamespace(
            state="RETRY",
            result=RuntimeError(
                json.dumps(
                    {
                        "sync_mode": "full",
                        "sync_phase": "waiting_for_rate_limit",
                        "activities_seen": 120,
                        "activities_processed": 98,
                        "activities_with_matches": 1,
                        "activities_skipped_no_polyline": 16,
                        "bag_rows_written": 1,
                        "cached_strava_activities": 98,
                        "total_strava_activities": 220,
                        "progress_is_indeterminate": True,
                        "progress_percentage": 81.7,
                        "started_at": "2026-05-13T13:00:00+00:00",
                        "updated_at": "2026-05-13T13:10:00+00:00",
                        "estimated_remaining_seconds": 360,
                        "rate_limit_wait_seconds": 240,
                        "rate_limit_scope": "short_window",
                        "read_window_limit": 100,
                        "read_window_usage": 100,
                        "read_window_remaining": 0,
                        "read_daily_limit": 1000,
                        "read_daily_usage": 742,
                        "read_daily_remaining": 258,
                        "read_window_resets_at": "2026-05-13T13:15:05+00:00",
                        "read_daily_resets_at": "2026-05-14T00:00:05+00:00",
                        "retry_after": "2026-05-13T13:14:00+00:00",
                        "message": "Waiting for Strava read limits.",
                    }
                )
            ),
        ),
    ):
        response = client.get("/api/v1/strava/tasks/task-retry")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-retry",
        "state": "RETRY",
        "message": "Waiting for Strava read limits.",
        "sync_mode": "full",
        "activities_seen": 120,
        "activities_processed": 98,
        "activities_with_matches": 1,
        "activities_skipped_no_polyline": 16,
        "activities_skipped_invalid_polyline": 0,
        "activities_skipped_unverified_elevation": 0,
        "bag_rows_written": 1,
        "cached_strava_activities": 98,
        "total_strava_activities": 220,
        "progress_percentage": 81.7,
        "progress_is_indeterminate": True,
        "sync_phase": "waiting_for_rate_limit",
        "started_at": "2026-05-13T13:00:00+00:00",
        "updated_at": "2026-05-13T13:10:00+00:00",
        "estimated_remaining_seconds": 360,
        "rate_limit_wait_seconds": 240,
        "rate_limit_scope": "short_window",
        "read_window_limit": 100,
        "read_window_usage": 100,
        "read_window_remaining": 0,
        "read_daily_limit": 1000,
        "read_daily_usage": 742,
        "read_daily_remaining": 258,
        "read_window_resets_at": "2026-05-13T13:15:05+00:00",
        "read_daily_resets_at": "2026-05-14T00:00:05+00:00",
        "retry_after": "2026-05-13T13:14:00+00:00",
        "is_complete": False,
        "is_error": False,
    }


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
        "app.api.routes.strava.enqueue_activity_sync",
        return_value=SimpleNamespace(id="task-789"),
    ) as mock_enqueue:
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
    mock_enqueue.assert_called_once_with(user_id, 555)


def test_receive_strava_webhook_event_returns_service_unavailable_when_enqueue_fails(
    client: TestClient,
) -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.scalar.return_value = SimpleNamespace(id=user_id, strava_athlete_id=321)
    app.dependency_overrides[get_db] = override_db(mock_db)

    with patch(
        "app.api.routes.strava.enqueue_activity_sync",
        side_effect=CeleryError("broker unavailable"),
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

    assert response.status_code == 503
    assert response.json()["detail"] == "Unable to queue the Strava webhook activity right now."
    assert mock_db.commit.called
