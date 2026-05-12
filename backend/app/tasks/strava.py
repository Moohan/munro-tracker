from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from celery import shared_task
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.session import SessionLocal
from app.models import StravaActivity, User
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
      AND CAST(:highest_point_metres AS NUMERIC(8, 2)) >= (m.height_metres - 20.0)
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
        user = _load_user(session, user_id)
        limit = activity_limit or get_settings().strava_sync_activity_limit
        client = _build_client_for_user(session, user)

        activity_ids = [
            int(activity.id)
            for activity in client.get_activities(limit=limit)
            if getattr(activity, "id", None) is not None
        ]
        result = _initial_result_payload(str(user.id), len(activity_ids))
        _process_activity_ids(
            session=session,
            user=user,
            client=client,
            activity_ids=activity_ids,
            result=result,
        )
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@shared_task(name="app.tasks.strava.sync_activity_for_user")
def sync_activity_for_user(
    user_id: str,
    activity_id: int,
) -> dict[str, object]:
    session = SessionLocal()
    try:
        user = _load_user(session, user_id)
        client = _build_client_for_user(session, user)

        result = _initial_result_payload(str(user.id), 1)
        _process_activity_ids(
            session=session,
            user=user,
            client=client,
            activity_ids=[int(activity_id)],
            result=result,
        )
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


def _initial_result_payload(user_id: str, activities_seen: int) -> dict[str, object]:
    return {
        "status": "completed",
        "user_id": user_id,
        "activities_seen": activities_seen,
        "activities_processed": 0,
        "activities_with_matches": 0,
        "activities_skipped_no_polyline": 0,
        "activities_skipped_invalid_polyline": 0,
        "activities_skipped_unverified_elevation": 0,
        "bag_rows_written": 0,
    }


def _load_user(session: Session, user_id: str) -> User:
    user_uuid = uuid.UUID(user_id)
    user = session.get(User, user_uuid)
    if user is None:
        raise ValueError(f"User {user_id} was not found.")
    return user


def _build_client_for_user(session: Session, user: User) -> Any:
    access_token, token_refreshed = ensure_fresh_access_token(session, user)
    if token_refreshed:
        session.commit()
    return build_authenticated_client(access_token)


def _process_activity_ids(
    *,
    session: Session,
    user: User,
    client: Any,
    activity_ids: Iterable[int],
    result: dict[str, object],
) -> None:
    for activity_id in activity_ids:
        activity_result = _process_single_activity(
            session=session,
            user=user,
            client=client,
            activity_id=int(activity_id),
        )
        session.commit()
        result["activities_processed"] = int(result["activities_processed"]) + 1

        if activity_result["status"] == "skipped_no_polyline":
            result["activities_skipped_no_polyline"] = (
                int(result["activities_skipped_no_polyline"]) + 1
            )
        elif activity_result["status"] == "skipped_invalid_polyline":
            result["activities_skipped_invalid_polyline"] = (
                int(result["activities_skipped_invalid_polyline"]) + 1
            )
        elif activity_result["status"] == "skipped_unverified_elevation":
            result["activities_skipped_unverified_elevation"] = (
                int(result["activities_skipped_unverified_elevation"]) + 1
            )

        bag_rows_written = int(activity_result["bag_rows_written"])
        result["bag_rows_written"] = int(result["bag_rows_written"]) + bag_rows_written
        if bag_rows_written > 0:
            result["activities_with_matches"] = (
                int(result["activities_with_matches"]) + 1
            )


def _process_single_activity(
    *,
    session: Session,
    user: User,
    client: Any,
    activity_id: int,
) -> dict[str, object]:
    detailed_activity = client.get_activity(activity_id)
    encoded_polyline = extract_activity_polyline(detailed_activity)
    highest_point_metres = _extract_highest_point_metres(
        client,
        activity_id,
        detailed_activity,
    )
    activity = _upsert_strava_activity(
        session=session,
        user_id=user.id,
        activity_id=activity_id,
        detailed_activity=detailed_activity,
        summary_polyline=encoded_polyline,
        highest_point_metres=highest_point_metres,
    )

    if not encoded_polyline:
        _mark_activity_status(
            activity,
            processing_status="skipped_no_polyline",
            processing_error="Activity did not include a summary polyline.",
        )
        return {"status": "skipped_no_polyline", "bag_rows_written": 0}

    try:
        coordinates = decode_polyline(encoded_polyline)
    except ValueError:
        _mark_activity_status(
            activity,
            processing_status="skipped_invalid_polyline",
            processing_error="Activity polyline could not be decoded.",
        )
        return {"status": "skipped_invalid_polyline", "bag_rows_written": 0}

    if not coordinates:
        _mark_activity_status(
            activity,
            processing_status="skipped_invalid_polyline",
            processing_error="Activity polyline did not contain coordinates.",
        )
        return {"status": "skipped_invalid_polyline", "bag_rows_written": 0}

    if highest_point_metres is None:
        _mark_activity_status(
            activity,
            processing_status="skipped_unverified_elevation",
            processing_error="Activity did not include a usable summit elevation reading.",
        )
        return {"status": "skipped_unverified_elevation", "bag_rows_written": 0}

    matches = _upsert_user_bags_for_track(
        session=session,
        user_id=user.id,
        activity_id=activity_id,
        bagged_at=_coerce_activity_time(
            getattr(detailed_activity, "start_date", None)
            or getattr(detailed_activity, "start_date_local", None)
        ),
        coordinates=coordinates,
        highest_point_metres=highest_point_metres,
    )
    _mark_activity_status(
        activity,
        processing_status="processed",
        processing_error=None,
    )
    return {"status": "processed", "bag_rows_written": len(matches)}


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
    highest_point_metres: float,
) -> list[dict[str, Any]]:
    track_wkt = _coordinates_to_wkt(coordinates)
    rows = session.execute(
        text(SUMMIT_MATCH_SQL),
        {
            "user_id": str(user_id),
            "activity_id": activity_id,
            "bagged_at": bagged_at,
            "track_wkt": track_wkt,
            "highest_point_metres": highest_point_metres,
        },
    ).mappings()
    return [dict(row) for row in rows]


def _upsert_strava_activity(
    *,
    session: Session,
    user_id: uuid.UUID,
    activity_id: int,
    detailed_activity: Any,
    summary_polyline: str | None,
    highest_point_metres: float | None,
) -> StravaActivity:
    activity = session.get(StravaActivity, (user_id, activity_id))
    if activity is None:
        activity = StravaActivity(user_id=user_id, strava_activity_id=activity_id)

    activity.name = _coerce_text(getattr(detailed_activity, "name", None))
    activity.sport_type = _coerce_text(
        getattr(detailed_activity, "sport_type", None)
        or getattr(detailed_activity, "type", None)
    )
    activity.started_at = _coerce_activity_time(
        getattr(detailed_activity, "start_date", None)
        or getattr(detailed_activity, "start_date_local", None)
    )
    activity.distance_metres = _to_decimal(getattr(detailed_activity, "distance", None))
    activity.total_elevation_gain_metres = _to_decimal(
        getattr(detailed_activity, "total_elevation_gain", None)
    )
    activity.highest_point_metres = _to_decimal(highest_point_metres)
    activity.summary_polyline = summary_polyline
    activity.processing_error = None
    activity.processing_status = activity.processing_status or "pending"
    session.add(activity)
    session.flush()
    return activity


def _mark_activity_status(
    activity: StravaActivity,
    *,
    processing_status: str,
    processing_error: str | None,
) -> None:
    activity.processing_status = processing_status
    activity.processing_error = processing_error
    activity.processed_at = datetime.now(timezone.utc)


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


def _extract_highest_point_metres(
    client: Any,
    activity_id: int,
    detailed_activity: Any,
) -> float | None:
    direct_high_point = _coerce_float(getattr(detailed_activity, "elev_high", None))
    if direct_high_point is not None:
        return direct_high_point

    stream_payload = _get_activity_streams(client, activity_id)
    altitude_values = _extract_stream_values(stream_payload, "altitude")
    if altitude_values:
        return max(altitude_values)

    return None


def _get_activity_streams(client: Any, activity_id: int) -> Any:
    get_streams = getattr(client, "get_activity_streams", None)
    if get_streams is None:
        return None

    for kwargs in (
        {"types": ["altitude", "latlng"], "key_by_type": True},
        {"types": ["altitude", "latlng"]},
        {"types": ["altitude"]},
    ):
        try:
            return get_streams(int(activity_id), **kwargs)
        except TypeError:
            continue
        except Exception:
            return None

    return None


def _extract_stream_values(stream_payload: Any, stream_name: str) -> list[float]:
    if stream_payload is None:
        return []

    values: Any = None
    if isinstance(stream_payload, dict):
        candidate = stream_payload.get(stream_name)
        values = getattr(candidate, "data", candidate)
    elif isinstance(stream_payload, list):
        for stream_item in stream_payload:
            stream_type = _coerce_text(
                getattr(stream_item, "type", None)
                or getattr(stream_item, "name", None)
            )
            if stream_type == stream_name:
                values = getattr(stream_item, "data", stream_item)
                break
    else:
        candidate = getattr(stream_payload, stream_name, None)
        values = getattr(candidate, "data", candidate)

    if not isinstance(values, (list, tuple)):
        return []

    extracted_values = [_coerce_float(value) for value in values]
    return [value for value in extracted_values if value is not None]


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None

    candidate = getattr(value, "num", value)
    try:
        return float(candidate)
    except (TypeError, ValueError):
        return None


def _to_decimal(value: Any) -> Decimal | None:
    numeric_value = _coerce_float(value)
    if numeric_value is None:
        return None
    return Decimal(f"{numeric_value:.2f}")


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None
