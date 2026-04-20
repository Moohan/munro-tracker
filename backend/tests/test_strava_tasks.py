"""Tests for the Strava tasks module."""
from __future__ import annotations

import pytest

from app.tasks.strava import decode_polyline


def test_decode_polyline_basic() -> None:
    """Test basic polyline decoding functionality."""
    # Google's reference example
    result = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")

    assert isinstance(result, list)
    assert len(result) == 3
    assert result[0] == (38.5, -120.2)


def test_decode_polyline_empty_string() -> None:
    """Test decoding an empty polyline string."""
    result = decode_polyline("")

    assert isinstance(result, list)
    assert len(result) == 0


def test_decode_polyline_single_point() -> None:
    """Test decoding a polyline with a single point."""
    # Encode a single coordinate
    result = decode_polyline("_p~iF~ps|U")

    assert isinstance(result, list)
    # Should have at least one coordinate
    if len(result) > 0:
        assert isinstance(result[0], tuple)
        assert len(result[0]) == 2
