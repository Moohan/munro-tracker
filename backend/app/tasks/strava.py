from __future__ import annotations

from dataclasses import dataclass
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, Iterable, Literal

from celery import Task, shared_task
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from stravalib.exc import Fault as StravaFault

from app.core.config import get_settings
from app.infrastructure.runtime_schema import (
    ensure_repeat_bag_schema,
    ensure_strava_sync_metadata_schema,
)
from app.infrastructure.session import SessionLocal
from app.models import StravaActivity, User
from app.services.strava import (
    build_authenticated_client,
    ensure_fresh_access_token,
    extract_activity_polyline,
    RateLimitObserver,
)

SyncMode = Literal["latest", "full", "activity", "totals"]
SyncPhase = Literal[
    "pending",
    "preparing",
    "discovering",
    "downloading",
    "processing",
    "waiting_for_rate_limit",
    "complete",
    "failed",
]
RateLimitScope = Literal["short_window", "daily"]

MAX_STRAVA_ACTIVITY_PAGE_SIZE = 200
STRAVA_ACTIVITY_TOTAL_STALE_AFTER = timedelta(hours=24)


@dataclass
class StravaReadQuotaObserver:
    read_window_limit: int = 0
    read_window_usage: int = 0
    read_daily_limit: int = 0
    read_daily_usage: int = 0
    observed_at: datetime | None = None

    def __call__(self, headers: dict[str, str], method: str) -> None:
        if str(method).upper() != "GET":
            return

        self.read_window_limit, self.read_daily_limit = _parse_rate_limit_pair(
            headers.get("X-ReadRateLimit-Limit")
        )
        self.read_window_usage, self.read_daily_usage = _parse_rate_limit_pair(
            headers.get("X-ReadRateLimit-Usage")
        )
        self.observed_at = datetime.now(timezone.utc)

    @property
    def read_window_remaining(self) -> int | None:
        if self.read_window_limit <= 0:
            return None
        return max(self.read_window_limit - self.read_window_usage, 0)

    @property
    def read_daily_remaining(self) -> int | None:
        if self.read_daily_limit <= 0:
            return None
        return max(self.read_daily_limit - self.read_daily_usage, 0)

    def has_snapshot(self) -> bool:
        return self.read_window_limit > 0 or self.read_daily_limit > 0

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
activity_match_rows AS (
    INSERT INTO user_bag_activities (
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
    ON CONFLICT (user_id, munro_id, source_activity_id) DO UPDATE
    SET
        bagged_at = LEAST(user_bag_activities.bagged_at, EXCLUDED.bagged_at),
        matched_distance_metres = CASE
            WHEN user_bag_activities.matched_distance_metres IS NULL THEN EXCLUDED.matched_distance_metres
            ELSE LEAST(user_bag_activities.matched_distance_metres, EXCLUDED.matched_distance_metres)
        END,
        summit_elevation_metres = COALESCE(
            user_bag_activities.summit_elevation_metres,
            EXCLUDED.summit_elevation_metres
        ),
        source = 'strava'
    RETURNING munro_id, source_activity_id, bagged_at, matched_distance_metres
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


@shared_task(
    bind=True,
    name="app.tasks.strava.sync_latest_activities_for_user",
)
def sync_latest_activities_for_user(
    self: Task,
    user_id: str,
    activity_limit: int | None = None,
) -> dict[str, object]:
    session = SessionLocal()
    result: dict[str, object] | None = None
    quota_observer = StravaReadQuotaObserver()
    try:
        user = _load_user(session, user_id)
        result = _initial_result_payload(str(user.id), 0, sync_mode="latest")
        _hydrate_sync_totals(session, user, result)
        _update_task_progress(self, result, message=_build_progress_message(result))
        bootstrap_limit = activity_limit or get_settings().strava_sync_activity_limit
        client = _build_client_for_user(session, user, rate_limit_observer=quota_observer)
        latest_total_is_fresh = _sync_total_is_fresh(user)

        for activity_batch in _iter_summary_activity_batches_for_latest_sync(
            session=session,
            user_id=user.id,
            client=client,
            activity_limit=bootstrap_limit,
            quota_observer=quota_observer,
            task=self,
            result=result,
        ):
            _apply_rate_limit_snapshot(result, quota_observer)
            result["activities_seen"] = int(result["activities_seen"]) + len(activity_batch)
            _update_task_progress(self, result, message=_build_progress_message(result))
            _process_activity_summaries(
                task=self,
                session=session,
                user=user,
                client=client,
                activity_batch=activity_batch,
                result=result,
                quota_observer=quota_observer,
                sync_mode="latest",
            )

        if latest_total_is_fresh and int(result.get("new_cached_activities", 0)) > 0:
            user.strava_activity_total_count = int(user.strava_activity_total_count or 0) + int(
                result["new_cached_activities"]
            )
            user.strava_activity_total_refreshed_at = datetime.now(timezone.utc)
            session.add(user)
            session.commit()
            _hydrate_sync_totals(session, user, result)

        return _complete_sync_task(result)
    except StravaFault as exc:
        session.rollback()
        _raise_for_retryable_rate_limit(
            task=self,
            exc=exc,
            result=result,
            user_id=user_id,
            sync_mode="latest",
            quota_observer=quota_observer,
        )
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@shared_task(
    bind=True,
    name="app.tasks.strava.sync_all_activities_for_user",
)
def sync_all_activities_for_user(
    self: Task,
    user_id: str,
) -> dict[str, object]:
    session = SessionLocal()
    result: dict[str, object] | None = None
    quota_observer = StravaReadQuotaObserver()
    try:
        user = _load_user(session, user_id)
        client = _build_client_for_user(session, user, rate_limit_observer=quota_observer)
        oldest_started_at = _get_oldest_cached_activity_started_at(session, user.id)
        result = _initial_result_payload(
            str(user.id),
            0,
            sync_mode="full",
            progress_is_indeterminate=True,
        )
        _hydrate_sync_totals(session, user, result)
        _update_task_progress(
            self,
            result,
            message=_build_progress_message(
                result,
                backfill_from_cache=oldest_started_at is not None,
            ),
        )

        if not _sync_total_is_fresh(user):
            _refresh_remote_activity_total(
                task=self,
                session=session,
                user=user,
                client=client,
                result=result,
                quota_observer=quota_observer,
                sync_mode="full",
            )
            _update_task_progress(
                self,
                result,
                message=_build_progress_message(
                    result,
                    backfill_from_cache=oldest_started_at is not None,
                ),
            )

        for activity_batch in _iter_summary_activity_batches_for_full_sync(
            session=session,
            user_id=user.id,
            client=client,
            quota_observer=quota_observer,
            task=self,
            result=result,
        ):
            _apply_rate_limit_snapshot(result, quota_observer)
            result["activities_seen"] = int(result["activities_seen"]) + len(activity_batch)
            _update_task_progress(
                self,
                result,
                message=_build_progress_message(
                    result,
                    backfill_from_cache=oldest_started_at is not None,
                ),
            )
            _process_activity_summaries(
                task=self,
                session=session,
                user=user,
                client=client,
                activity_batch=activity_batch,
                result=result,
                quota_observer=quota_observer,
                sync_mode="full",
                backfill_from_cache=oldest_started_at is not None,
            )
        return _complete_sync_task(
            result,
            backfill_from_cache=oldest_started_at is not None,
        )
    except StravaFault as exc:
        session.rollback()
        _raise_for_retryable_rate_limit(
            task=self,
            exc=exc,
            result=result,
            user_id=user_id,
            sync_mode="full",
            progress_is_indeterminate=True,
            quota_observer=quota_observer,
        )
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@shared_task(
    bind=True,
    name="app.tasks.strava.refresh_activity_totals_for_user",
)
def refresh_activity_totals_for_user(
    self: Task,
    user_id: str,
) -> dict[str, object]:
    session = SessionLocal()
    result: dict[str, object] | None = None
    quota_observer = StravaReadQuotaObserver()
    try:
        user = _load_user(session, user_id)
        client = _build_client_for_user(session, user, rate_limit_observer=quota_observer)
        result = _initial_result_payload(
            str(user.id),
            0,
            sync_mode="totals",
            progress_is_indeterminate=True,
        )
        _hydrate_sync_totals(session, user, result)
        _update_task_progress(self, result, message=_build_progress_message(result))
        _refresh_remote_activity_total(
            task=self,
            session=session,
            user=user,
            client=client,
            result=result,
            quota_observer=quota_observer,
            sync_mode="totals",
        )
        return _complete_sync_task(result)
    except StravaFault as exc:
        session.rollback()
        _raise_for_retryable_rate_limit(
            task=self,
            exc=exc,
            result=result,
            user_id=user_id,
            sync_mode="totals",
            progress_is_indeterminate=True,
            quota_observer=quota_observer,
        )
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@shared_task(
    bind=True,
    name="app.tasks.strava.sync_activity_for_user",
)
def sync_activity_for_user(
    self: Task,
    user_id: str,
    activity_id: int,
) -> dict[str, object]:
    session = SessionLocal()
    result: dict[str, object] | None = None
    quota_observer = StravaReadQuotaObserver()
    try:
        user = _load_user(session, user_id)
        client = _build_client_for_user(session, user, rate_limit_observer=quota_observer)

        result = _initial_result_payload(str(user.id), 1, sync_mode="activity")
        _hydrate_sync_totals(session, user, result)
        _update_task_progress(self, result, message=_build_progress_message(result))
        _process_activity_ids(
            task=self,
            session=session,
            user=user,
            client=client,
            activity_ids=[int(activity_id)],
            result=result,
            quota_observer=quota_observer,
            sync_mode="activity",
        )
        if _sync_total_is_fresh(user) and int(result.get("new_cached_activities", 0)) > 0:
            user.strava_activity_total_count = int(user.strava_activity_total_count or 0) + int(
                result["new_cached_activities"]
            )
            user.strava_activity_total_refreshed_at = datetime.now(timezone.utc)
            session.add(user)
            session.commit()
            _hydrate_sync_totals(session, user, result)
        return _complete_sync_task(result)
    except StravaFault as exc:
        session.rollback()
        _raise_for_retryable_rate_limit(
            task=self,
            exc=exc,
            result=result,
            user_id=user_id,
            sync_mode="activity",
            quota_observer=quota_observer,
        )
        raise
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


def _initial_result_payload(
    user_id: str,
    activities_seen: int,
    *,
    sync_mode: SyncMode,
    progress_is_indeterminate: bool = False,
) -> dict[str, object]:
    current_time = _isoformat_utc(datetime.now(timezone.utc))
    return {
        "status": "in_progress",
        "user_id": user_id,
        "sync_mode": sync_mode,
        "activities_seen": activities_seen,
        "activities_processed": 0,
        "activities_with_matches": 0,
        "activities_skipped_no_polyline": 0,
        "activities_skipped_invalid_polyline": 0,
        "activities_skipped_unverified_elevation": 0,
        "bag_rows_written": 0,
        "cached_strava_activities": None,
        "total_strava_activities": None,
        "new_cached_activities": 0,
        "progress_percentage": 0.0,
        "progress_is_indeterminate": progress_is_indeterminate,
        "sync_phase": "preparing",
        "started_at": current_time,
        "updated_at": current_time,
        "estimated_remaining_seconds": None,
        "rate_limit_wait_seconds": None,
        "rate_limit_scope": None,
        "read_window_limit": None,
        "read_window_usage": None,
        "read_window_remaining": None,
        "read_daily_limit": None,
        "read_daily_usage": None,
        "read_daily_remaining": None,
        "read_window_resets_at": None,
        "read_daily_resets_at": None,
        "retry_after": None,
        "message": "Preparing your Strava sync.",
    }


def _load_user(session: Session, user_id: str) -> User:
    ensure_strava_sync_metadata_schema(session)
    user_uuid = uuid.UUID(user_id)
    user = session.get(User, user_uuid)
    if user is None:
        raise ValueError(f"User {user_id} was not found.")
    return user


def _build_client_for_user(
    session: Session,
    user: User,
    *,
    rate_limit_observer: RateLimitObserver | None = None,
) -> Any:
    access_token, token_refreshed = ensure_fresh_access_token(session, user)
    if token_refreshed:
        session.commit()
    return build_authenticated_client(
        access_token,
        rate_limit_observer=rate_limit_observer,
    )


def _count_cached_activities(session: Session, user_id: uuid.UUID) -> int:
    cached_count = session.scalar(
        select(func.count(StravaActivity.strava_activity_id)).where(StravaActivity.user_id == user_id)
    )
    return int(cached_count or 0)


def _sync_total_is_fresh(user: User, *, now: datetime | None = None) -> bool:
    if user.strava_activity_total_count is None:
        return False

    refreshed_at = _coerce_cached_activity_timestamp(user.strava_activity_total_refreshed_at)
    if refreshed_at is None:
        return False

    current_time = now or datetime.now(timezone.utc)
    return current_time - refreshed_at <= STRAVA_ACTIVITY_TOTAL_STALE_AFTER


def _hydrate_sync_totals(
    session: Session,
    user: User,
    result: dict[str, object],
) -> None:
    cached_count = _count_cached_activities(session, user.id)
    result["cached_strava_activities"] = cached_count
    result["total_strava_activities"] = (
        int(user.strava_activity_total_count)
        if user.strava_activity_total_count is not None
        else None
    )

    if str(result.get("sync_mode") or "") == "full":
        result["progress_is_indeterminate"] = (
            result["total_strava_activities"] in {None, 0}
        )


def _apply_rate_limit_snapshot(
    result: dict[str, object],
    quota_observer: StravaReadQuotaObserver,
    *,
    now: datetime | None = None,
) -> None:
    if not quota_observer.has_snapshot():
        return

    current_time = now or quota_observer.observed_at or datetime.now(timezone.utc)
    result["read_window_limit"] = quota_observer.read_window_limit
    result["read_window_usage"] = quota_observer.read_window_usage
    result["read_window_remaining"] = quota_observer.read_window_remaining
    result["read_daily_limit"] = quota_observer.read_daily_limit
    result["read_daily_usage"] = quota_observer.read_daily_usage
    result["read_daily_remaining"] = quota_observer.read_daily_remaining
    result["read_window_resets_at"] = _isoformat_utc(
        current_time + timedelta(seconds=_seconds_until_next_quarter(current_time))
    )
    result["read_daily_resets_at"] = _isoformat_utc(
        current_time + timedelta(seconds=_seconds_until_next_day(current_time))
    )


def _raise_for_observed_rate_limit(
    *,
    task: Task,
    quota_observer: StravaReadQuotaObserver,
    result: dict[str, object],
    user_id: str,
    sync_mode: SyncMode,
) -> None:
    if not quota_observer.has_snapshot():
        return

    now = datetime.now(timezone.utc)
    _apply_rate_limit_snapshot(result, quota_observer, now=now)

    scope: RateLimitScope | None = None
    countdown: int | None = None
    if quota_observer.read_daily_remaining is not None and quota_observer.read_daily_remaining <= 0:
        scope = "daily"
        countdown = max(_seconds_until_next_day(now) + 5, 60)
    elif (
        quota_observer.read_window_remaining is not None
        and quota_observer.read_window_remaining <= 0
    ):
        scope = "short_window"
        countdown = max(_seconds_until_next_quarter(now) + 5, 60)

    if scope is None or countdown is None:
        return

    retry_payload = _build_rate_limit_retry_payload(
        result=result,
        user_id=user_id,
        sync_mode=sync_mode,
        countdown=countdown,
        progress_is_indeterminate=bool(result.get("progress_is_indeterminate")),
        rate_limit_scope=scope,
        quota_observer=quota_observer,
    )
    raise task.retry(exc=RuntimeError(json.dumps(retry_payload)), countdown=countdown)


def _iter_summary_activity_batches(
    *,
    client: Any,
    activity_query: dict[str, object],
    page_size: int,
    quota_observer: StravaReadQuotaObserver,
    task: Task,
    result: dict[str, object],
    user_id: str,
    sync_mode: SyncMode,
) -> Iterable[list[Any]]:
    activity_iterator = client.get_activities(**activity_query)
    if hasattr(activity_iterator, "per_page"):
        try:
            requested_limit = activity_query.get("limit")
            if isinstance(requested_limit, int) and requested_limit > 0:
                activity_iterator.per_page = min(max(page_size, 1), requested_limit)
            else:
                activity_iterator.per_page = max(page_size, 1)
        except Exception:
            pass

    activity_batch: list[Any] = []
    while True:
        if not bool(getattr(activity_iterator, "_buffer", None)):
            _raise_for_observed_rate_limit(
                task=task,
                quota_observer=quota_observer,
                result=result,
                user_id=user_id,
                sync_mode=sync_mode,
            )

        try:
            activity = next(activity_iterator)
        except StopIteration:
            break

        if getattr(activity, "id", None) is None:
            continue

        activity_batch.append(activity)
        if len(activity_batch) >= max(page_size, 1):
            yield activity_batch
            activity_batch = []

    if activity_batch:
        yield activity_batch


def _iter_summary_activity_batches_for_latest_sync(
    *,
    session: Session,
    user_id: uuid.UUID,
    client: Any,
    activity_limit: int,
    quota_observer: StravaReadQuotaObserver,
    task: Task,
    result: dict[str, object],
) -> Iterable[list[Any]]:
    latest_started_at = _get_latest_cached_activity_started_at(session, user_id)
    activity_query = _build_latest_activity_query(
        latest_started_at=latest_started_at,
        activity_limit=activity_limit,
    )
    page_size = (
        min(activity_limit, MAX_STRAVA_ACTIVITY_PAGE_SIZE)
        if latest_started_at is None and activity_limit > 0
        else MAX_STRAVA_ACTIVITY_PAGE_SIZE
    )
    return _iter_summary_activity_batches(
        client=client,
        activity_query=activity_query,
        page_size=page_size,
        quota_observer=quota_observer,
        task=task,
        result=result,
        user_id=str(user_id),
        sync_mode="latest",
    )


def _iter_summary_activity_batches_for_full_sync(
    *,
    session: Session,
    user_id: uuid.UUID,
    client: Any,
    quota_observer: StravaReadQuotaObserver,
    task: Task,
    result: dict[str, object],
) -> Iterable[list[Any]]:
    activity_query = _build_full_history_activity_query(
        oldest_started_at=_get_oldest_cached_activity_started_at(session, user_id)
    )
    return _iter_summary_activity_batches(
        client=client,
        activity_query=activity_query,
        page_size=MAX_STRAVA_ACTIVITY_PAGE_SIZE,
        quota_observer=quota_observer,
        task=task,
        result=result,
        user_id=str(user_id),
        sync_mode="full",
    )


def _refresh_remote_activity_total(
    *,
    task: Task,
    session: Session,
    user: User,
    client: Any,
    result: dict[str, object],
    quota_observer: StravaReadQuotaObserver,
    sync_mode: SyncMode,
) -> int:
    result["sync_phase"] = "discovering"
    result["progress_is_indeterminate"] = True
    result["message"] = _build_progress_message(result)
    _update_task_progress(task, result, message=_build_progress_message(result))

    total_count = 0
    counting_result = result if sync_mode == "totals" else dict(result)
    counting_result["activities_seen"] = 0
    counting_result["activities_processed"] = 0

    for activity_batch in _iter_summary_activity_batches(
        client=client,
        activity_query={"before": None, "limit": None},
        page_size=MAX_STRAVA_ACTIVITY_PAGE_SIZE,
        quota_observer=quota_observer,
        task=task,
        result=result,
        user_id=str(user.id),
        sync_mode=sync_mode,
    ):
        total_count += len(activity_batch)
        result["total_strava_activities"] = total_count
        _apply_rate_limit_snapshot(result, quota_observer)
        if sync_mode == "totals":
            result["activities_seen"] = total_count
            result["activities_processed"] = total_count
        _update_task_progress(task, result, message=_build_progress_message(result))

    user.strava_activity_total_count = total_count
    user.strava_activity_total_refreshed_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()
    _hydrate_sync_totals(session, user, result)
    if sync_mode != "totals":
        result["sync_phase"] = "preparing"
    return total_count


def _list_activity_ids_for_latest_sync(
    *,
    session: Session,
    user_id: uuid.UUID,
    client: Any,
    activity_limit: int,
) -> list[int]:
    latest_started_at = _get_latest_cached_activity_started_at(session, user_id)
    activity_query = _build_latest_activity_query(
        latest_started_at=latest_started_at,
        activity_limit=activity_limit,
    )
    return [
        int(activity.id)
        for activity in client.get_activities(**activity_query)
        if getattr(activity, "id", None) is not None
    ]


def _iter_activity_ids_for_full_sync(
    *,
    session: Session,
    user_id: uuid.UUID,
    client: Any,
) -> Iterable[int]:
    oldest_started_at = _get_oldest_cached_activity_started_at(session, user_id)
    activity_query = _build_full_history_activity_query(oldest_started_at=oldest_started_at)
    return (
        int(activity.id)
        for activity in client.get_activities(**activity_query)
        if getattr(activity, "id", None) is not None
    )


def _iter_activity_id_batches_for_full_sync(
    *,
    session: Session,
    user_id: uuid.UUID,
    client: Any,
    batch_size: int,
) -> Iterable[list[int]]:
    activity_query = _build_full_history_activity_query(
        oldest_started_at=_get_oldest_cached_activity_started_at(session, user_id)
    )
    activity_iterator = client.get_activities(**activity_query)
    if hasattr(activity_iterator, "per_page"):
        try:
            activity_iterator.per_page = max(batch_size, 1)
        except Exception:
            pass

    activity_batch: list[int] = []

    for activity in activity_iterator:
        activity_id = getattr(activity, "id", None)
        if activity_id is None:
            continue

        activity_batch.append(int(activity_id))
        if len(activity_batch) >= max(batch_size, 1):
            yield activity_batch
            activity_batch = []

    if activity_batch:
        yield activity_batch


def _get_latest_cached_activity_started_at(
    session: Session,
    user_id: uuid.UUID,
) -> datetime | None:
    latest_started_at = session.scalar(
        select(func.max(StravaActivity.started_at)).where(StravaActivity.user_id == user_id)
    )
    return _coerce_cached_activity_timestamp(latest_started_at)


def _get_oldest_cached_activity_started_at(
    session: Session,
    user_id: uuid.UUID,
) -> datetime | None:
    oldest_started_at = session.scalar(
        select(func.min(StravaActivity.started_at)).where(StravaActivity.user_id == user_id)
    )
    return _coerce_cached_activity_timestamp(oldest_started_at)


def _coerce_cached_activity_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_latest_activity_query(
    *,
    latest_started_at: datetime | None,
    activity_limit: int,
) -> dict[str, object]:
    if latest_started_at is None:
        return {
            "after": None,
            "limit": activity_limit,
        }

    return {
        "after": latest_started_at - timedelta(minutes=5),
        "limit": None,
    }


def _build_full_history_activity_query(
    *,
    oldest_started_at: datetime | None,
) -> dict[str, object]:
    if oldest_started_at is None:
        return {
            "before": None,
            "limit": None,
        }

    return {
        "before": oldest_started_at + timedelta(minutes=5),
        "limit": None,
    }


def _process_activity_ids(
    *,
    task: Task | None,
    session: Session,
    user: User,
    client: Any,
    activity_ids: Iterable[int],
    result: dict[str, object],
    quota_observer: StravaReadQuotaObserver | None = None,
    sync_mode: SyncMode = "activity",
    increment_seen_during_processing: bool = False,
    backfill_from_cache: bool = False,
) -> None:
    for activity_id in activity_ids:
        if increment_seen_during_processing:
            result["activities_seen"] = int(result["activities_seen"]) + 1

        activity_result = _process_single_activity(
            session=session,
            user=user,
            client=client,
            activity_id=int(activity_id),
            task=task,
            result=result,
            quota_observer=quota_observer,
            sync_mode=sync_mode,
        )
        session.commit()
        _record_activity_result(result, activity_result)
        if quota_observer is not None:
            _apply_rate_limit_snapshot(result, quota_observer)

        _update_task_progress(
            task,
            result,
            message=_build_progress_message(
                result,
                backfill_from_cache=backfill_from_cache,
            ),
        )


def _record_activity_result(
    result: dict[str, object],
    activity_result: dict[str, object],
) -> None:
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
        result["activities_with_matches"] = int(result["activities_with_matches"]) + 1

    if activity_result.get("new_cached_activity"):
        result["new_cached_activities"] = int(result.get("new_cached_activities", 0)) + 1
        cached_activities = result.get("cached_strava_activities")
        if isinstance(cached_activities, int):
            result["cached_strava_activities"] = cached_activities + 1


def _process_single_activity(
    *,
    session: Session,
    user: User,
    client: Any,
    activity_id: int,
    task: Task | None = None,
    result: dict[str, object] | None = None,
    quota_observer: StravaReadQuotaObserver | None = None,
    sync_mode: SyncMode = "activity",
) -> dict[str, object]:
    if (
        quota_observer is not None
        and task is not None
        and result is not None
    ):
        _raise_for_observed_rate_limit(
            task=task,
            quota_observer=quota_observer,
            result=result,
            user_id=str(user.id),
            sync_mode=sync_mode,
        )

    detailed_activity = client.get_activity(activity_id)
    encoded_polyline = extract_activity_polyline(detailed_activity)
    highest_point_metres = _extract_highest_point_metres(
        client,
        activity_id,
        detailed_activity,
        quota_observer=quota_observer,
        task=task,
        result=result,
        user_id=str(user.id),
        sync_mode=sync_mode,
    )
    activity, new_cached_activity = _upsert_strava_activity(
        session=session,
        user_id=user.id,
        activity_id=activity_id,
        activity_payload=detailed_activity,
        summary_polyline=encoded_polyline,
        highest_point_metres=highest_point_metres,
    )

    if not encoded_polyline:
        _mark_activity_status(
            activity,
            processing_status="skipped_no_polyline",
            processing_error="Activity did not include a summary polyline.",
        )
        return {
            "status": "skipped_no_polyline",
            "bag_rows_written": 0,
            "new_cached_activity": new_cached_activity,
        }

    try:
        coordinates = decode_polyline(encoded_polyline)
    except ValueError:
        _mark_activity_status(
            activity,
            processing_status="skipped_invalid_polyline",
            processing_error="Activity polyline could not be decoded.",
        )
        return {
            "status": "skipped_invalid_polyline",
            "bag_rows_written": 0,
            "new_cached_activity": new_cached_activity,
        }

    if not coordinates:
        _mark_activity_status(
            activity,
            processing_status="skipped_invalid_polyline",
            processing_error="Activity polyline did not contain coordinates.",
        )
        return {
            "status": "skipped_invalid_polyline",
            "bag_rows_written": 0,
            "new_cached_activity": new_cached_activity,
        }

    if highest_point_metres is None:
        _mark_activity_status(
            activity,
            processing_status="skipped_unverified_elevation",
            processing_error="Activity did not include a usable summit elevation reading.",
        )
        return {
            "status": "skipped_unverified_elevation",
            "bag_rows_written": 0,
            "new_cached_activity": new_cached_activity,
        }

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
    return {
        "status": "processed",
        "bag_rows_written": len(matches),
        "new_cached_activity": new_cached_activity,
    }


def _process_activity_summaries(
    *,
    task: Task,
    session: Session,
    user: User,
    client: Any,
    activity_batch: Iterable[Any],
    result: dict[str, object],
    quota_observer: StravaReadQuotaObserver,
    sync_mode: SyncMode,
    backfill_from_cache: bool = False,
) -> None:
    for activity_summary in activity_batch:
        activity_result = _process_summary_activity(
            session=session,
            user=user,
            client=client,
            activity_summary=activity_summary,
            task=task,
            result=result,
            quota_observer=quota_observer,
            sync_mode=sync_mode,
        )
        session.commit()
        _record_activity_result(result, activity_result)
        _apply_rate_limit_snapshot(result, quota_observer)
        _update_task_progress(
            task,
            result,
            message=_build_progress_message(
                result,
                backfill_from_cache=backfill_from_cache,
            ),
        )


def _process_summary_activity(
    *,
    session: Session,
    user: User,
    client: Any,
    activity_summary: Any,
    task: Task,
    result: dict[str, object],
    quota_observer: StravaReadQuotaObserver,
    sync_mode: SyncMode,
) -> dict[str, object]:
    activity_id = int(getattr(activity_summary, "id"))
    encoded_polyline = extract_activity_polyline(activity_summary)
    highest_point_metres = _coerce_float(getattr(activity_summary, "elev_high", None))
    required_stream_types: list[str] = []
    if highest_point_metres is None:
        required_stream_types.append("altitude")
    if not encoded_polyline:
        required_stream_types.append("latlng")

    stream_payload = None
    if required_stream_types:
        stream_payload = _get_activity_streams(
            client,
            activity_id,
            required_stream_types=tuple(required_stream_types),
            quota_observer=quota_observer,
            task=task,
            result=result,
            user_id=str(user.id),
            sync_mode=sync_mode,
        )

    if highest_point_metres is None:
        altitude_values = _extract_stream_values(stream_payload, "altitude")
        if altitude_values:
            highest_point_metres = max(altitude_values)

    activity, new_cached_activity = _upsert_strava_activity(
        session=session,
        user_id=user.id,
        activity_id=activity_id,
        activity_payload=activity_summary,
        summary_polyline=encoded_polyline,
        highest_point_metres=highest_point_metres,
    )

    if encoded_polyline:
        try:
            coordinates = decode_polyline(encoded_polyline)
        except ValueError:
            _mark_activity_status(
                activity,
                processing_status="skipped_invalid_polyline",
                processing_error="Activity polyline could not be decoded.",
            )
            return {
                "status": "skipped_invalid_polyline",
                "bag_rows_written": 0,
                "new_cached_activity": new_cached_activity,
            }
    else:
        coordinates = _extract_latlng_coordinates(stream_payload)

    if not coordinates:
        _mark_activity_status(
            activity,
            processing_status="skipped_no_polyline",
            processing_error="Activity did not include a usable map trace.",
        )
        return {
            "status": "skipped_no_polyline",
            "bag_rows_written": 0,
            "new_cached_activity": new_cached_activity,
        }

    if highest_point_metres is None:
        _mark_activity_status(
            activity,
            processing_status="skipped_unverified_elevation",
            processing_error="Activity did not include a usable summit elevation reading.",
        )
        return {
            "status": "skipped_unverified_elevation",
            "bag_rows_written": 0,
            "new_cached_activity": new_cached_activity,
        }

    matches = _upsert_user_bags_for_track(
        session=session,
        user_id=user.id,
        activity_id=activity_id,
        bagged_at=_coerce_activity_time(
            getattr(activity_summary, "start_date", None)
            or getattr(activity_summary, "start_date_local", None)
        ),
        coordinates=coordinates,
        highest_point_metres=highest_point_metres,
    )
    _mark_activity_status(
        activity,
        processing_status="processed",
        processing_error=None,
    )
    return {
        "status": "processed",
        "bag_rows_written": len(matches),
        "new_cached_activity": new_cached_activity,
    }


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
    ensure_repeat_bag_schema(session)
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
    activity_payload: Any,
    summary_polyline: str | None,
    highest_point_metres: float | None,
) -> tuple[StravaActivity, bool]:
    existing_activity = session.get(StravaActivity, (user_id, activity_id))
    activity_values = {
        "user_id": user_id,
        "strava_activity_id": activity_id,
        "name": _coerce_text(getattr(activity_payload, "name", None)),
        "sport_type": _coerce_text(
            getattr(activity_payload, "sport_type", None)
            or getattr(activity_payload, "type", None)
        ),
        "started_at": _coerce_activity_time(
            getattr(activity_payload, "start_date", None)
            or getattr(activity_payload, "start_date_local", None)
        ),
        "distance_metres": _to_decimal(getattr(activity_payload, "distance", None)),
        "total_elevation_gain_metres": _to_decimal(
            getattr(activity_payload, "total_elevation_gain", None)
        ),
        "highest_point_metres": _to_decimal(highest_point_metres),
        "summary_polyline": summary_polyline,
        "processing_status": "pending",
        "processing_error": None,
    }
    upsert_stmt = pg_insert(StravaActivity).values(**activity_values)
    session.execute(
        upsert_stmt.on_conflict_do_update(
            index_elements=[StravaActivity.user_id, StravaActivity.strava_activity_id],
            set_={
                "name": activity_values["name"],
                "sport_type": activity_values["sport_type"],
                "started_at": activity_values["started_at"],
                "distance_metres": activity_values["distance_metres"],
                "total_elevation_gain_metres": activity_values["total_elevation_gain_metres"],
                "highest_point_metres": activity_values["highest_point_metres"],
                "summary_polyline": activity_values["summary_polyline"],
                "processing_status": activity_values["processing_status"],
                "processing_error": activity_values["processing_error"],
            },
        )
    )
    activity = session.get(StravaActivity, (user_id, activity_id))
    if activity is None:
        raise ValueError(
            f"Unable to load Strava activity {activity_id} for user {user_id} after upsert."
        )
    return activity, existing_activity is None


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
    *,
    quota_observer: StravaReadQuotaObserver | None = None,
    task: Task | None = None,
    result: dict[str, object] | None = None,
    user_id: str | None = None,
    sync_mode: SyncMode = "activity",
) -> float | None:
    direct_high_point = _coerce_float(getattr(detailed_activity, "elev_high", None))
    if direct_high_point is not None:
        return direct_high_point

    stream_payload = _get_activity_streams(
        client,
        activity_id,
        required_stream_types=("altitude",),
        quota_observer=quota_observer,
        task=task,
        result=result,
        user_id=user_id,
        sync_mode=sync_mode,
    )
    altitude_values = _extract_stream_values(stream_payload, "altitude")
    if altitude_values:
        return max(altitude_values)

    return None


def _get_activity_streams(
    client: Any,
    activity_id: int,
    *,
    required_stream_types: tuple[str, ...] = ("altitude", "latlng"),
    quota_observer: StravaReadQuotaObserver | None = None,
    task: Task | None = None,
    result: dict[str, object] | None = None,
    user_id: str | None = None,
    sync_mode: SyncMode = "activity",
) -> Any:
    get_streams = getattr(client, "get_activity_streams", None)
    if get_streams is None:
        return None

    if (
        quota_observer is not None
        and task is not None
        and result is not None
        and user_id is not None
    ):
        _raise_for_observed_rate_limit(
            task=task,
            quota_observer=quota_observer,
            result=result,
            user_id=user_id,
            sync_mode=sync_mode,
        )

    requested_types = list(dict.fromkeys(required_stream_types))
    for kwargs in (
        {"types": requested_types, "key_by_type": True},
        {"types": requested_types},
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


def _extract_latlng_coordinates(stream_payload: Any) -> list[tuple[float, float]]:
    if stream_payload is None:
        return []

    values: Any = None
    if isinstance(stream_payload, dict):
        candidate = stream_payload.get("latlng")
        values = getattr(candidate, "data", candidate)
    elif isinstance(stream_payload, list):
        for stream_item in stream_payload:
            stream_type = _coerce_text(
                getattr(stream_item, "type", None)
                or getattr(stream_item, "name", None)
            )
            if stream_type == "latlng":
                values = getattr(stream_item, "data", stream_item)
                break
    else:
        candidate = getattr(stream_payload, "latlng", None)
        values = getattr(candidate, "data", candidate)

    if not isinstance(values, (list, tuple)):
        return []

    coordinates: list[tuple[float, float]] = []
    for coordinate_pair in values:
        if not isinstance(coordinate_pair, (list, tuple)) or len(coordinate_pair) != 2:
            continue
        latitude = _coerce_float(coordinate_pair[0])
        longitude = _coerce_float(coordinate_pair[1])
        if latitude is None or longitude is None:
            continue
        coordinates.append((latitude, longitude))
    return coordinates


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


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _update_task_progress(
    task: Task | None,
    result: dict[str, object],
    *,
    message: str,
) -> None:
    current_time = datetime.now(timezone.utc)
    result["message"] = message
    result["sync_phase"] = _resolve_sync_phase(result)
    result["updated_at"] = _isoformat_utc(current_time)
    result["estimated_remaining_seconds"] = _estimate_remaining_seconds(
        result,
        now=current_time,
    )

    result["progress_percentage"] = _calculate_progress_percentage(result)

    if task is None:
        return

    task.update_state(
        state="PROGRESS",
        meta={
            **result,
            "message": message,
        },
    )


def _calculate_progress_percentage(result: dict[str, object]) -> float:
    sync_mode = str(result.get("sync_mode") or "latest")
    total_strava_activities = _coerce_optional_int(result.get("total_strava_activities"))
    cached_strava_activities = _coerce_optional_int(result.get("cached_strava_activities"))
    activities_processed = int(result.get("activities_processed") or 0)
    activities_seen = int(result.get("activities_seen") or 0)

    if sync_mode == "full" and total_strava_activities and cached_strava_activities is not None:
        return round(
            (min(cached_strava_activities, total_strava_activities) / total_strava_activities) * 100,
            1,
        )

    if sync_mode == "totals":
        return 100.0 if result.get("status") == "completed" else 0.0

    if activities_seen <= 0:
        return 100.0 if activities_processed > 0 else 0.0

    return round((activities_processed / activities_seen) * 100, 1)


def _build_progress_message(
    result: dict[str, object],
    *,
    is_complete: bool = False,
    backfill_from_cache: bool = False,
) -> str:
    sync_mode = str(result.get("sync_mode") or "latest")
    sync_phase = str(result.get("sync_phase") or "")
    activities_processed = int(result.get("activities_processed") or 0)
    activities_seen = int(result.get("activities_seen") or 0)
    activities_with_matches = int(result["activities_with_matches"])
    total_strava_activities = _coerce_optional_int(result.get("total_strava_activities"))
    activity_label = "activity" if activities_seen == 1 else "activities"
    match_label = "match" if activities_with_matches == 1 else "matches"

    if is_complete:
        if sync_mode == "totals":
            if total_strava_activities is None:
                return "Strava activity total refreshed."
            return f"Strava activity total refreshed. Found {total_strava_activities} activities."

        if sync_mode == "full":
            if activities_seen <= 0:
                if backfill_from_cache:
                    return (
                        "Full Strava history sync complete. No older activities were left "
                        "to synchronise."
                    )
                return (
                    "Full Strava history sync complete. No Strava activities were available "
                    "to synchronise."
                )

            return (
                f"Full Strava history sync complete. Checked {activities_seen} activities "
                f"and found {activities_with_matches} Munro {match_label}."
            )

        if sync_mode == "activity":
            return (
                f"Strava activity sync complete. Checked {activities_seen} {activity_label} "
                f"and found {activities_with_matches} Munro {match_label}."
            )

        if activities_seen <= 0:
            return "Strava connected. No recent activities were available to synchronise."

        return (
            f"Latest Strava sync complete. Checked {activities_seen} activities and found "
            f"{activities_with_matches} Munro {match_label}."
        )

    if sync_phase == "waiting_for_rate_limit":
        return "Waiting for Strava read limits."

    if sync_mode == "totals" or sync_phase == "discovering":
        return "Counting your Strava history."

    if sync_mode == "full":
        if activities_seen <= 0:
            if backfill_from_cache:
                return "Downloading your older Strava activities."
            return "Downloading your Strava activities."

        return "Syncing your full Strava history."

    if sync_mode == "activity":
        return "Processing a newly detected Strava activity."

    if activities_seen <= 0:
        return "Downloading your latest Strava activities."

    return f"Syncing your latest {activity_label}."


def _complete_sync_task(
    result: dict[str, object],
    *,
    backfill_from_cache: bool = False,
) -> dict[str, object]:
    result["status"] = "completed"
    result["progress_is_indeterminate"] = False
    result["sync_phase"] = "complete"
    result["progress_percentage"] = 100.0
    result["estimated_remaining_seconds"] = 0
    result["rate_limit_wait_seconds"] = None
    result["rate_limit_scope"] = None
    result["retry_after"] = None
    result["updated_at"] = _isoformat_utc(datetime.now(timezone.utc))
    result["message"] = _build_progress_message(
        result,
        is_complete=True,
        backfill_from_cache=backfill_from_cache,
    )
    return result


def _resolve_sync_phase(result: dict[str, object]) -> SyncPhase:
    if result.get("status") == "completed":
        return "complete"

    if int(result.get("rate_limit_wait_seconds") or 0) > 0:
        return "waiting_for_rate_limit"

    sync_mode = str(result.get("sync_mode") or "latest")
    current_sync_phase = str(result.get("sync_phase") or "")
    activities_seen = int(result.get("activities_seen") or 0)
    activities_processed = int(result.get("activities_processed") or 0)

    if current_sync_phase == "discovering" or sync_mode == "totals":
        return "discovering"

    if sync_mode == "activity":
        return "processing"

    if activities_seen <= 0:
        return "downloading"

    if activities_processed < activities_seen:
        return "processing"

    if sync_mode == "full" and bool(result.get("progress_is_indeterminate")):
        return "downloading"

    return "processing"


def _estimate_remaining_seconds(
    result: dict[str, object],
    *,
    now: datetime,
) -> int | None:
    rate_limit_wait_seconds = int(result.get("rate_limit_wait_seconds") or 0)
    sync_mode = str(result.get("sync_mode") or "latest")
    activities_processed = int(result.get("activities_processed") or 0)
    activities_seen = int(result.get("activities_seen") or 0)
    total_strava_activities = _coerce_optional_int(result.get("total_strava_activities"))
    cached_strava_activities = _coerce_optional_int(result.get("cached_strava_activities"))
    known_remaining_activities = max(activities_seen - activities_processed, 0)

    if sync_mode == "full" and total_strava_activities and cached_strava_activities is not None:
        known_remaining_activities = max(total_strava_activities - cached_strava_activities, 0)
    elif sync_mode == "totals":
        known_remaining_activities = 0

    if result.get("status") == "completed":
        return 0

    started_at = _parse_iso_datetime(result.get("started_at"))
    if activities_processed <= 0 or started_at is None:
        return rate_limit_wait_seconds or None

    elapsed_seconds = max(int((now - started_at).total_seconds()), 1)
    processing_seconds = round((elapsed_seconds / activities_processed) * known_remaining_activities)

    if processing_seconds <= 0:
        processing_seconds = 0 if known_remaining_activities == 0 else 1

    if rate_limit_wait_seconds > 0:
        return rate_limit_wait_seconds + processing_seconds

    if known_remaining_activities <= 0:
        return None

    return processing_seconds


def _build_rate_limit_retry_payload(
    *,
    result: dict[str, object] | None,
    user_id: str,
    sync_mode: SyncMode,
    countdown: int,
    progress_is_indeterminate: bool,
    rate_limit_scope: RateLimitScope,
    quota_observer: StravaReadQuotaObserver | None = None,
) -> dict[str, object]:
    retry_after = datetime.now(timezone.utc) + timedelta(seconds=countdown)

    payload = dict(result or _initial_result_payload(user_id, 0, sync_mode=sync_mode))
    payload["user_id"] = user_id
    payload["sync_mode"] = sync_mode
    payload["progress_is_indeterminate"] = progress_is_indeterminate or bool(
        payload.get("progress_is_indeterminate")
    )
    payload["sync_phase"] = "waiting_for_rate_limit"
    payload["rate_limit_wait_seconds"] = countdown
    payload["rate_limit_scope"] = rate_limit_scope
    payload["retry_after"] = _isoformat_utc(retry_after)
    payload["updated_at"] = _isoformat_utc(datetime.now(timezone.utc))
    payload["message"] = "Waiting for Strava read limits."
    if quota_observer is not None:
        _apply_rate_limit_snapshot(payload, quota_observer)
    payload["estimated_remaining_seconds"] = _estimate_remaining_seconds(
        payload,
        now=datetime.now(timezone.utc),
    )
    return payload


def _raise_for_retryable_rate_limit(
    *,
    task: Task,
    exc: StravaFault,
    result: dict[str, object] | None,
    user_id: str,
    sync_mode: SyncMode,
    progress_is_indeterminate: bool = False,
    quota_observer: StravaReadQuotaObserver | None = None,
) -> None:
    if not _is_retryable_strava_rate_limit(exc):
        return

    countdown = _get_strava_retry_delay_seconds(exc)
    rate_limit_scope = _get_strava_retry_scope(exc)
    retry_quota_observer = quota_observer or _quota_observer_from_headers(
        getattr(getattr(exc, "response", None), "headers", {}) or {}
    )
    retry_payload = _build_rate_limit_retry_payload(
        result=result,
        user_id=user_id,
        sync_mode=sync_mode,
        countdown=countdown,
        progress_is_indeterminate=progress_is_indeterminate,
        rate_limit_scope=rate_limit_scope,
        quota_observer=retry_quota_observer,
    )
    raise task.retry(exc=RuntimeError(json.dumps(retry_payload)), countdown=countdown)


def _is_retryable_strava_rate_limit(exc: StravaFault) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 429


def _get_strava_retry_delay_seconds(
    exc: StravaFault,
    *,
    now: datetime | None = None,
) -> int:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    short_limit, long_limit = _parse_rate_limit_pair(headers.get("X-ReadRateLimit-Limit"))
    short_usage, long_usage = _parse_rate_limit_pair(headers.get("X-ReadRateLimit-Usage"))

    current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    if long_limit and long_usage >= long_limit:
        return max(_seconds_until_next_day(current_time) + 5, 60)
    if short_limit and short_usage >= short_limit:
        return max(_seconds_until_next_quarter(current_time) + 5, 60)
    return 15 * 60


def _get_strava_retry_scope(exc: StravaFault) -> RateLimitScope:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    short_limit, long_limit = _parse_rate_limit_pair(headers.get("X-ReadRateLimit-Limit"))
    short_usage, long_usage = _parse_rate_limit_pair(headers.get("X-ReadRateLimit-Usage"))
    if long_limit and long_usage >= long_limit:
        return "daily"
    if short_limit and short_usage >= short_limit:
        return "short_window"
    return "short_window"


def _quota_observer_from_headers(headers: dict[str, str]) -> StravaReadQuotaObserver:
    quota_observer = StravaReadQuotaObserver()
    quota_observer(headers, "GET")
    return quota_observer


def _parse_rate_limit_pair(value: Any) -> tuple[int, int]:
    if not value:
        return 0, 0

    try:
        left, right = str(value).split(",", 1)
        return int(left.strip()), int(right.strip())
    except (TypeError, ValueError):
        return 0, 0


def _seconds_until_next_quarter(now: datetime) -> int:
    next_quarter_minute = ((now.minute // 15) + 1) * 15
    if next_quarter_minute >= 60:
        next_boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_boundary = now.replace(
            minute=next_quarter_minute,
            second=0,
            microsecond=0,
        )
    return int((next_boundary - now).total_seconds())


def _seconds_until_next_day(now: datetime) -> int:
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((next_day - now).total_seconds())


def _parse_iso_datetime(value: object) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    try:
        parsed_value = datetime.fromisoformat(str(value))
    except ValueError:
        return None

    if parsed_value.tzinfo is None:
        return parsed_value.replace(tzinfo=timezone.utc)
    return parsed_value.astimezone(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _build_rate_limit_retry_message(
    sync_mode: SyncMode,
    countdown: int,
) -> str:
    del sync_mode, countdown
    return "Waiting for Strava read limits."
