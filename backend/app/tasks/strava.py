from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from celery import shared_task
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.session import SessionLocal
from app.models import User
from app.services.strava import (
    build_authenticated_client,
    ensure_fresh_access_token,
    extract_activity_polyline,
)

SUMMIT_MATCH_SQL = """
WITH submitted_track AS (
    SELECT
        ST_GeomFromText(:track_wkt, 4326) AS geom,
        CAST(:user_id AS UUID) AS user_id,
        CAST(:activity_id AS BIGINT) AS activity_id,
        CAST(:bagged_at AS TIMESTAMPTZ) AS bagged_at
),
matched_summits AS (
    SELECT
        t.user_id,
        m.id AS munro_id,
        t.activity_id,
        t.bagged_at,
        ST_Distance(t.geom::geography, m.geom::geography) AS matched_distance_metres,
        m.height_metres AS summit_elevation_metres
    FROM munros AS m
    CROSS JOIN submitted_track AS t
    WHERE m.is_munro_top = FALSE
      AND ST_DWithin(t.geom::geography, m.geom::geography, 100)
)
INSERT INTO user_bags (
    user_id,
    munro_id,
    source_activity_id,
    bagged_at,
    matched_distance_metres,
    summit_elevation_metres,
    source
)
SELECT
    user_id,
    munro_id,
    activity_id,
    bagged_at,
    matched_distance_metres,
    summit_elevation_metres,
    'strava'
FROM matched_summits
ON CONFLICT (user_id, munro_id) DO UPDATE
SET
    bagged_at = LEAST(user_bags.bagged_at, EXCLUDED.bagged_at),
    matched_distance_metres = CASE
        WHEN user_bags.matched_distance_metres IS NULL THEN EXCLUDED.matched_distance_metres
        ELSE LEAST(user_bags.matched_distance_metres, EXCLUDED.matched_distance_metres)
    END,
    summit_elevation_metres = COALESCE(
        user_bags.summit_elevation_metres,
        EXCLUDED.summit_elevation_metres
    ),
    source_activity_id = CASE
        WHEN EXCLUDED.bagged_at < user_bags.bagged_at THEN EXCLUDED.source_activity_id
        ELSE user_bags.source_activity_id
    END,
    source = 'strava'
RETURNING munro_id, source_activity_id, bagged_at, matched_distance_metres;
""".strip()


@shared_task(name="app.tasks.strava.sync_latest_activities_for_user")
def sync_latest_activities_for_user(
    user_id: str,
    activity_limit: int | None = None,
) -> dict[str, object]:
    session = SessionLocal()
    try:
        user_uuid: uuid.UUID = uuid.UUID(user_id)
        user = session.get(User, user_uuid)
        if user is None:
            raise ValueError(f"User {user_id} was not found.")

        limit = activity_limit or get_settings().strava_sync_activity_limit
        access_token: str
        token_refreshed: bool
        access_token, token_refreshed = ensure_fresh_access_token(session, user)
        if token_refreshed:
            # Persist the rotated refresh token immediately so subsequent runs
            # never fall back to an invalidated token.
            session.commit()

        client = build_authenticated_client(access_token)
        activities = list(client.get_activities(limit=limit))

        result = {
            "status": "completed",
            "user_id": str(user.id),
            "activities_seen": len(activities),
            "activities_processed": 0,
            "activities_with_matches": 0,
            "activities_skipped_no_polyline": 0,
            "activities_skipped_invalid_polyline": 0,
            "bag_rows_written": 0,
        }

        for activity_summary in activities:
            activity_id = getattr(activity_summary, "id", None)
            if activity_id is None:
                result["activities_skipped_invalid_polyline"] += 1
                continue

            detailed_activity = client.get_activity(int(activity_id))
            encoded_polyline = extract_activity_polyline(detailed_activity)
            if not encoded_polyline:
                result["activities_skipped_no_polyline"] += 1
                continue

            try:
                coordinates = decode_polyline(encoded_polyline)
            except ValueError:
                session.rollback()
                result["activities_skipped_invalid_polyline"] += 1
                continue

            if not coordinates:
                result["activities_skipped_invalid_polyline"] += 1
                continue

            matches = _upsert_user_bags_for_track(
                session=session,
                user_id=user.id,
                activity_id=int(activity_id),
                bagged_at=_coerce_activity_time(
                    getattr(detailed_activity, "start_date", None)
                    or getattr(detailed_activity, "start_date_local", None)
                ),
                coordinates=coordinates,
            )
            session.commit()

            result["activities_processed"] += 1
            result["bag_rows_written"] += len(matches)
            if matches:
                result["activities_with_matches"] += 1

        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def decode_polyline(encoded_polyline: str) -> list[tuple[float, float]]:
    if not encoded_polyline:
        return []

    coordinates: list[tuple[float, float]] = []
    index = 0
    latitude = 0
    longitude = 0

    while index < len(encoded_polyline):
        latitude, index = _decode_polyline_value(encoded_polyline, index, latitude)
        longitude, index = _decode_polyline_value(encoded_polyline, index, longitude)
        coordinates.append((latitude / 1e5, longitude / 1e5))

    return coordinates


def _decode_polyline_value(
    encoded_polyline: str,
    index: int,
    previous_value: int,
) -> tuple[int, int]:
    result = 0
    shift = 0

    while True:
        if index >= len(encoded_polyline):
            raise ValueError("Polyline ended unexpectedly while decoding coordinates.")

        value = ord(encoded_polyline[index]) - 63
        index += 1
        result |= (value & 0x1F) << shift
        shift += 5

        if value < 0x20:
            break

    delta = ~(result >> 1) if result & 1 else result >> 1
    return previous_value + delta, index


def _upsert_user_bags_for_track(
    *,
    session: Session,
    user_id: uuid.UUID,
    activity_id: int,
    bagged_at: datetime,
    coordinates: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    track_wkt = _coordinates_to_wkt(coordinates)
    rows = session.execute(
        text(SUMMIT_MATCH_SQL),
        {
            "user_id": str(user_id),
            "activity_id": activity_id,
            "bagged_at": bagged_at,
            "track_wkt": track_wkt,
        },
    ).mappings()
    return [dict(row) for row in rows]


def _coordinates_to_wkt(coordinates: list[tuple[float, float]]) -> str:
    if len(coordinates) == 1:
        latitude, longitude = coordinates[0]
        return f"POINT({longitude:.5f} {latitude:.5f})"

    points = ", ".join(
        f"{longitude:.5f} {latitude:.5f}" for latitude, longitude in coordinates
    )
    return f"LINESTRING({points})"


def _coerce_activity_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.now(timezone.utc)
