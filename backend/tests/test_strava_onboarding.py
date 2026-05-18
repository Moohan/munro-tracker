from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from celery.exceptions import CeleryError

from app.services.strava_onboarding import onboard_user_from_strava_callback


def test_onboard_user_from_strava_callback_enqueues_initial_sync() -> None:
    db = MagicMock()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        strava_athlete_id=2409676,
        display_name="James Hayes",
    )

    with patch(
        "app.services.strava_onboarding.parse_oauth_state",
        return_value={"next_url": "http://localhost:5173/"},
    ), patch(
        "app.services.strava_onboarding.parse_scope_string",
        return_value={"read", "activity:read_all"},
    ), patch(
        "app.services.strava_onboarding.ensure_required_scopes",
    ), patch(
        "app.services.strava_onboarding.exchange_code_for_token",
        return_value={"access_token": "access-token"},
    ), patch(
        "app.services.strava_onboarding.upsert_user_from_auth",
        return_value=user,
    ), patch(
        "app.services.strava_onboarding.enqueue_latest_activities_sync",
        return_value=SimpleNamespace(id="task-123"),
    ) as mock_enqueue:
        payload = onboard_user_from_strava_callback(
            db,
            code="code-123",
            state="state-123",
            scope="read,activity:read_all",
        )

    assert payload["sync_enqueued"] is True
    assert payload["sync_task_id"] == "task-123"
    assert payload["redirect_to"] == "http://localhost:5173/"
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(user)
    mock_enqueue.assert_called_once_with(user.id)


def test_onboard_user_from_strava_callback_continues_when_enqueue_fails() -> None:
    db = MagicMock()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        strava_athlete_id=2409676,
        display_name="James Hayes",
    )

    with patch(
        "app.services.strava_onboarding.parse_oauth_state",
        return_value={"next_url": "http://localhost:5173/"},
    ), patch(
        "app.services.strava_onboarding.parse_scope_string",
        return_value={"read", "activity:read_all"},
    ), patch(
        "app.services.strava_onboarding.ensure_required_scopes",
    ), patch(
        "app.services.strava_onboarding.exchange_code_for_token",
        return_value={"access_token": "access-token"},
    ), patch(
        "app.services.strava_onboarding.upsert_user_from_auth",
        return_value=user,
    ), patch(
        "app.services.strava_onboarding.enqueue_latest_activities_sync",
        side_effect=CeleryError("broker unavailable"),
    ):
        payload = onboard_user_from_strava_callback(
            db,
            code="code-123",
            state="state-123",
            scope="read,activity:read_all",
        )

    assert payload["sync_enqueued"] is False
    assert payload["sync_task_id"] is None
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(user)
