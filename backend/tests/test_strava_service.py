from __future__ import annotations

import time

import pytest

from app.services.strava import (
    create_oauth_state,
    ensure_required_scopes,
    get_strava_oauth_status,
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
    monkeypatch.delenv("STRAVA_WEBHOOK_VERIFY_TOKEN", raising=False)
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
