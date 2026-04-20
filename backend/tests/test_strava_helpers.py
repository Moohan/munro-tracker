from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services.strava import create_oauth_state, parse_oauth_state
from app.tasks.strava import decode_polyline


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_decode_polyline_google_reference_example() -> None:
    coordinates = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")

    assert coordinates == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_oauth_state_round_trip_respects_frontend_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-value")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")

    state = create_oauth_state("/connected")
    payload = parse_oauth_state(state)

    assert payload["next_url"] == "http://localhost:5173/connected"


def test_oauth_state_rejects_untrusted_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret-value")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")

    with pytest.raises(ValueError, match="frontend origin"):
        create_oauth_state("https://example.com/elsewhere")
