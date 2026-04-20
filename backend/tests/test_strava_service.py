"""Tests for the Strava service module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.strava import (
    create_oauth_state,
    parse_oauth_state,
    parse_scope_string,
    ensure_required_scopes,
)


def test_strava_service_oauth_state_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that OAuth state can be created."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "test-client-123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "test-secret-456")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")

    from app.core.config import get_settings
    get_settings.cache_clear()

    state = create_oauth_state("/profile")
    assert isinstance(state, str)
    assert "." in state  # State should have signature format


def test_parse_scope_string_basic() -> None:
    """Test parsing scope strings."""
    result = parse_scope_string("read,activity:read_all")
    assert isinstance(result, set)
    assert "read" in result
    assert "activity:read_all" in result


def test_parse_scope_string_empty() -> None:
    """Test parsing empty scope string."""
    result = parse_scope_string("")
    assert isinstance(result, set)
    assert len(result) == 0


def test_ensure_required_scopes_valid() -> None:
    """Test that required scopes validation passes with valid scopes."""
    try:
        ensure_required_scopes(["read", "activity:read_all"])
        # Should not raise
    except ValueError:
        pytest.fail("Should not raise ValueError for valid scopes")


def test_ensure_required_scopes_missing() -> None:
    """Test that required scopes validation fails with missing scopes."""
    with pytest.raises(ValueError, match="missing required scope"):
        ensure_required_scopes(["read"])
