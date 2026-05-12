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
    db.execute.side_effect = [
        MagicMock(scalar_one=MagicMock(return_value=282)),
        MagicMock(scalar_one=MagicMock(return_value=12)),
        MagicMock(scalar_one=MagicMock(return_value=datetime(2025, 1, 1, tzinfo=timezone.utc))),
        MagicMock(scalar_one=MagicMock(return_value=Decimal("3450.50"))),
    ]

    result = build_dashboard_summary(db, uuid.uuid4())

    assert result["total_munros"] == 282
    assert result["bagged_munros"] == 12
    assert result["completion_percentage"] == Decimal("4.3")
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
                "total_ascent_metres": Decimal("1234.50"),
                "last_bagged_at": None,
            },
        ):
            response = TestClient(app).get(f"/api/v1/users/{user_id}/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["total_munros"] == 282
    assert response.json()["total_ascent_metres"] == 1234.5
