from __future__ import annotations

import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from app.services.strava import (
    build_authenticated_client,
    create_oauth_state,
    ensure_required_scopes,
    exchange_code_for_token,
    get_strava_oauth_status,
    save_strava_oauth_settings,
    get_strava_webhook_verify_token,
    parse_oauth_state,
    parse_scope_string,
)


def test_create_oauth_state_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")

    state = create_oauth_state("/dashboard")
    payload = parse_oauth_state(state)

    assert payload["next_url"] == "http://localhost:5173/dashboard"


def test_parse_oauth_state_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("STRAVA_OAUTH_STATE_TTL_SECONDS", "1")

    state = create_oauth_state("/dashboard")
    time.sleep(1.1)

    with pytest.raises(ValueError, match="expired"):
        parse_oauth_state(state)


def test_parse_scope_string_basic() -> None:
    assert parse_scope_string("read,activity:read_all") == {"read", "activity:read_all"}


def test_ensure_required_scopes_missing() -> None:
    with pytest.raises(ValueError, match="missing required scope"):
        ensure_required_scopes(["read"])


def test_get_strava_webhook_verify_token_prefers_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_WEBHOOK_SECRET", "legacy-secret")
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "explicit-token")

    assert get_strava_webhook_verify_token() == "explicit-token"


def test_get_strava_webhook_verify_token_falls_back_to_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "")
    monkeypatch.setenv("STRAVA_WEBHOOK_SECRET", "legacy-secret")

    assert get_strava_webhook_verify_token() == "legacy-secret"


def test_get_strava_oauth_status_reports_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")

    status = get_strava_oauth_status()

    assert status == {
        "oauth_available": True,
        "reason": None,
        "message": "Strava OAuth is available.",
    }


def test_get_strava_oauth_status_reports_missing_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "0")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")

    status = get_strava_oauth_status()

    assert status["oauth_available"] is False
    assert status["reason"] == "missing_client_id"
    assert "STRAVA_CLIENT_ID" in str(status["message"])


def test_get_strava_oauth_status_reports_missing_client_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "replace-me")

    status = get_strava_oauth_status()

    assert status["oauth_available"] is False
    assert status["reason"] == "missing_client_secret"
    assert "STRAVA_CLIENT_SECRET" in str(status["message"])


def test_get_strava_oauth_status_prefers_saved_dashboard_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "0")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "replace-me")
    db = MagicMock()
    db.execute.side_effect = [
        MagicMock(),
        MagicMock(scalar_one_or_none=MagicMock(return_value="456")),
        MagicMock(scalar_one_or_none=MagicMock(return_value="stored-secret")),
    ]

    status = get_strava_oauth_status(db)

    assert status == {
        "oauth_available": True,
        "reason": None,
        "message": "Strava OAuth is available.",
    }


def test_save_strava_oauth_settings_writes_both_values() -> None:
    db = MagicMock()

    save_strava_oauth_settings(
        db,
        client_id=789,
        client_secret=" stored-secret ",
    )

    assert db.execute.call_count == 3


def test_exchange_code_for_token_requests_athlete_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    athlete = MagicMock()
    athlete.model_dump.return_value = {"id": 42, "firstname": "Molly"}
    strava_client = MagicMock()
    strava_client.exchange_code_for_token.return_value = (
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_at": 1715529600,
        },
        athlete,
    )

    with patch("app.services.strava.Client", return_value=strava_client):
        token_bundle = exchange_code_for_token("authorisation-code")

    strava_client.exchange_code_for_token.assert_called_once_with(
        client_id=123,
        client_secret="secret",
        code="authorisation-code",
        return_athlete=True,
    )
    assert token_bundle["athlete"]["id"] == 42


def test_exchange_code_for_token_fetches_athlete_when_strava_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    strava_client = MagicMock()
    strava_client.exchange_code_for_token.return_value = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": 1715529600,
    }
    authenticated_client = MagicMock()
    athlete = MagicMock()
    athlete.model_dump.return_value = {"id": 99, "firstname": "Hamish"}
    authenticated_client.get_athlete.return_value = athlete

    with patch("app.services.strava.Client", return_value=strava_client), patch(
        "app.services.strava.build_authenticated_client",
        return_value=authenticated_client,
    ) as mock_build_client:
        token_bundle = exchange_code_for_token("authorisation-code")

    mock_build_client.assert_called_once_with("access-token")
    authenticated_client.get_athlete.assert_called_once_with()
    assert token_bundle["athlete"]["id"] == 99


def test_build_authenticated_client_disables_builtin_rate_limit_sleeping() -> None:
    with patch("app.services.strava.Client") as mock_client:
        build_authenticated_client("access-token")

    mock_client.assert_called_once_with(
        access_token="access-token",
        rate_limit_requests=False,
    )


def test_build_authenticated_client_accepts_a_rate_limit_observer() -> None:
    observer = MagicMock()

    with patch("app.services.strava.Client") as mock_client:
        build_authenticated_client("access-token", rate_limit_observer=observer)

    mock_client.assert_called_once_with(
        access_token="access-token",
        rate_limiter=observer,
    )
