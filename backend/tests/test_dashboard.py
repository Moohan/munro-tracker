from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.infrastructure.session import get_db
from app.main import app
from app.services.dashboard import build_dashboard_summary


def override_db(mock_db: MagicMock):
    def _override():
        yield mock_db

    return _override


def test_build_dashboard_summary_aggregates_counts_and_ascent() -> None:
    db = MagicMock()
    db.get.return_value = SimpleNamespace(
        strava_activity_total_count=220,
        strava_activity_total_refreshed_at=datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc),
    )
    db.execute.side_effect = [
        MagicMock(scalar_one=MagicMock(return_value=282)),
        MagicMock(scalar_one=MagicMock(return_value=12)),
        MagicMock(scalar_one=MagicMock(return_value=datetime(2025, 1, 1, tzinfo=timezone.utc))),
        MagicMock(scalar_one=MagicMock(return_value=25)),
        MagicMock(scalar_one=MagicMock(return_value=Decimal("3450.50"))),
    ]

    result = build_dashboard_summary(db, uuid.uuid4())

    assert result["total_munros"] == 282
    assert result["bagged_munros"] == 12
    assert result["completion_percentage"] == Decimal("4.3")
    assert result["cached_strava_activities"] == 25
    assert result["total_strava_activities"] == 220
    assert result["total_strava_activities_updated_at"] == datetime(
        2026, 5, 13, 9, 30, tzinfo=timezone.utc
    )
    assert result["total_ascent_metres"] == Decimal("3450.50")


def test_dashboard_route_returns_user_summary() -> None:
    user_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.get.return_value = SimpleNamespace(id=user_id)
    app.dependency_overrides[get_db] = override_db(mock_db)

    try:
        with patch(
            "app.api.routes.users.build_dashboard_summary",
            return_value={
                "total_munros": 282,
                "bagged_munros": 10,
                "completion_percentage": Decimal("3.5"),
                "cached_strava_activities": 18,
                "total_strava_activities": 220,
                "total_strava_activities_updated_at": "2026-05-13T09:30:00+00:00",
                "total_ascent_metres": Decimal("1234.50"),
                "last_bagged_at": None,
            },
        ):
            response = TestClient(app).get(f"/api/v1/users/{user_id}/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["total_munros"] == 282
    assert response.json()["cached_strava_activities"] == 18
    assert response.json()["total_strava_activities"] == 220
    assert response.json()["total_strava_activities_updated_at"] == "2026-05-13T09:30:00Z"
    assert response.json()["total_ascent_metres"] == 1234.5
