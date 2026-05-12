from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tasks.strava import _extract_highest_point_metres, decode_polyline


def test_decode_polyline_google_reference_example() -> None:
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_decode_polyline_raises_for_incomplete_data() -> None:
    with pytest.raises(ValueError, match="ended unexpectedly"):
        decode_polyline("_p~iF~ps|U_ulL")


def test_extract_highest_point_metres_prefers_activity_field() -> None:
    client = SimpleNamespace()
    activity = SimpleNamespace(elev_high=1245.0)

    assert _extract_highest_point_metres(client, 1, activity) == 1245.0


def test_extract_highest_point_metres_uses_altitude_stream_fallback() -> None:
    client = SimpleNamespace(
        get_activity_streams=lambda *_args, **_kwargs: {
            "altitude": SimpleNamespace(data=[650.0, 812.5, 801.0])
        }
    )
    activity = SimpleNamespace(elev_high=None)

    assert _extract_highest_point_metres(client, 1, activity) == 812.5
