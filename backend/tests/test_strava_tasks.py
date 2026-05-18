from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.tasks.strava import (
    StravaReadQuotaObserver,
    _extract_highest_point_metres,
    _iter_summary_activity_batches_for_full_sync,
    _process_summary_activity,
    _raise_for_observed_rate_limit,
    _get_strava_retry_delay_seconds,
    _iter_activity_id_batches_for_full_sync,
    _iter_activity_ids_for_full_sync,
    _list_activity_ids_for_latest_sync,
    _raise_for_retryable_rate_limit,
    decode_polyline,
    sync_all_activities_for_user,
    sync_activity_for_user,
    sync_latest_activities_for_user,
)
from app.worker.celery_app import celery_app


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
    get_activity_streams = MagicMock(
        return_value={"altitude": SimpleNamespace(data=[650.0, 812.5, 801.0])}
    )
    client = SimpleNamespace(get_activity_streams=get_activity_streams)
    activity = SimpleNamespace(elev_high=None)

    assert _extract_highest_point_metres(client, 1, activity) == 812.5
    get_activity_streams.assert_called_once_with(1, types=["altitude"], key_by_type=True)


def test_sync_tasks_route_to_the_default_celery_queue() -> None:
    latest_route = celery_app.amqp.router.route(
        {},
        sync_latest_activities_for_user.name,
        args=("user-123",),
        kwargs={},
    )
    full_route = celery_app.amqp.router.route(
        {},
        sync_all_activities_for_user.name,
        args=("user-123",),
        kwargs={},
    )
    activity_route = celery_app.amqp.router.route(
        {},
        sync_activity_for_user.name,
        args=("user-123", 123),
        kwargs={},
    )

    assert latest_route["queue"].name == "celery"
    assert full_route["queue"].name == "celery"
    assert activity_route["queue"].name == "celery"


def test_list_activity_ids_for_latest_sync_uses_bootstrap_limit() -> None:
    recorded_kwargs: dict[str, object] = {}

    def get_activities(**kwargs: object) -> list[SimpleNamespace]:
        recorded_kwargs.update(kwargs)
        return [SimpleNamespace(id=101), SimpleNamespace(id=202)]

    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: None)
    client = SimpleNamespace(get_activities=get_activities)

    activity_ids = _list_activity_ids_for_latest_sync(
        session=session,
        user_id=uuid.uuid4(),
        client=client,
        activity_limit=25,
    )

    assert activity_ids == [101, 202]
    assert recorded_kwargs == {"after": None, "limit": 25}


def test_list_activity_ids_for_latest_sync_uses_incremental_watermark() -> None:
    latest_started_at = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    recorded_kwargs: dict[str, object] = {}

    def get_activities(**kwargs: object) -> list[SimpleNamespace]:
        recorded_kwargs.update(kwargs)
        return [SimpleNamespace(id=303), SimpleNamespace(id=None)]

    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: latest_started_at)
    client = SimpleNamespace(get_activities=get_activities)

    activity_ids = _list_activity_ids_for_latest_sync(
        session=session,
        user_id=uuid.uuid4(),
        client=client,
        activity_limit=25,
    )

    assert activity_ids == [303]
    assert recorded_kwargs == {
        "after": latest_started_at - timedelta(minutes=5),
        "limit": None,
    }


def test_iter_activity_ids_for_full_sync_fetches_entire_history_when_cache_is_empty() -> None:
    recorded_kwargs: dict[str, object] = {}

    def get_activities(**kwargs: object) -> list[SimpleNamespace]:
        recorded_kwargs.update(kwargs)
        return [SimpleNamespace(id=404), SimpleNamespace(id=505)]

    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: None)
    client = SimpleNamespace(get_activities=get_activities)

    activity_ids = list(
        _iter_activity_ids_for_full_sync(
            session=session,
            user_id=uuid.uuid4(),
            client=client,
        )
    )

    assert activity_ids == [404, 505]
    assert recorded_kwargs == {"before": None, "limit": None}


def test_iter_activity_ids_for_full_sync_uses_oldest_cached_watermark() -> None:
    oldest_started_at = datetime(2024, 1, 3, 7, 30, tzinfo=timezone.utc)
    recorded_kwargs: dict[str, object] = {}

    def get_activities(**kwargs: object) -> list[SimpleNamespace]:
        recorded_kwargs.update(kwargs)
        return [SimpleNamespace(id=606), SimpleNamespace(id=None)]

    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: oldest_started_at)
    client = SimpleNamespace(get_activities=get_activities)

    activity_ids = list(
        _iter_activity_ids_for_full_sync(
            session=session,
            user_id=uuid.uuid4(),
            client=client,
        )
    )

    assert activity_ids == [606]
    assert recorded_kwargs == {
        "before": oldest_started_at + timedelta(minutes=5),
        "limit": None,
    }


def test_iter_activity_id_batches_for_full_sync_groups_downloaded_ids() -> None:
    oldest_started_at = datetime(2024, 1, 3, 7, 30, tzinfo=timezone.utc)
    recorded_kwargs: dict[str, object] = {}

    def get_activities(**kwargs: object) -> list[SimpleNamespace]:
        recorded_kwargs.update(kwargs)
        return [
            SimpleNamespace(id=701),
            SimpleNamespace(id=702),
            SimpleNamespace(id=703),
            SimpleNamespace(id=None),
        ]

    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: oldest_started_at)
    client = SimpleNamespace(get_activities=get_activities)

    activity_batches = list(
        _iter_activity_id_batches_for_full_sync(
            session=session,
            user_id=uuid.uuid4(),
            client=client,
            batch_size=2,
        )
    )

    assert activity_batches == [[701, 702], [703]]
    assert recorded_kwargs == {
        "before": oldest_started_at + timedelta(minutes=5),
        "limit": None,
    }


def test_iter_summary_activity_batches_for_full_sync_uses_max_page_size() -> None:
    class FakeIterator:
        def __init__(self) -> None:
            self._items = [SimpleNamespace(id=101), SimpleNamespace(id=202)]
            self._buffer = [object()]
            self.per_page = 1

        def __iter__(self) -> "FakeIterator":
            return self

        def __next__(self) -> SimpleNamespace:
            if not self._items:
                raise StopIteration
            return self._items.pop(0)

    iterator = FakeIterator()
    client = SimpleNamespace(get_activities=MagicMock(return_value=iterator))
    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: None)

    batches = list(
        _iter_summary_activity_batches_for_full_sync(
            session=session,
            user_id=uuid.uuid4(),
            client=client,
            quota_observer=StravaReadQuotaObserver(),
            task=MagicMock(),
            result={"sync_mode": "full"},
        )
    )

    assert [[activity.id for activity in batch] for batch in batches] == [[101, 202]]
    client.get_activities.assert_called_once_with(before=None, limit=None)
    assert iterator.per_page == 200


def test_get_strava_retry_delay_seconds_uses_next_quarter_for_short_limit() -> None:
    response = SimpleNamespace(
        status_code=429,
        headers={
            "X-ReadRateLimit-Limit": "200,2000",
            "X-ReadRateLimit-Usage": "200,450",
        },
    )
    exc = SimpleNamespace(response=response)

    countdown = _get_strava_retry_delay_seconds(
        exc,  # type: ignore[arg-type]
        now=datetime(2026, 5, 13, 13, 14, 30, tzinfo=timezone.utc),
    )

    assert countdown == 60


def test_raise_for_observed_rate_limit_retries_before_the_next_read() -> None:
    task = MagicMock()
    task.retry.side_effect = RuntimeError("retry invoked")
    quota_observer = StravaReadQuotaObserver()
    quota_observer(
        {
            "X-ReadRateLimit-Limit": "100,1000",
            "X-ReadRateLimit-Usage": "100,742",
        },
        "GET",
    )
    result = {
        "status": "in_progress",
        "user_id": "user-123",
        "sync_mode": "full",
        "activities_seen": 95,
        "activities_processed": 95,
        "progress_is_indeterminate": True,
        "started_at": "2026-05-13T13:00:00+00:00",
        "updated_at": "2026-05-13T13:10:00+00:00",
    }

    with pytest.raises(RuntimeError, match="retry invoked"):
        _raise_for_observed_rate_limit(
            task=task,
            quota_observer=quota_observer,
            result=result,
            user_id="user-123",
            sync_mode="full",
        )

    retry_payload = json.loads(str(task.retry.call_args.kwargs["exc"]))
    assert retry_payload["rate_limit_scope"] == "short_window"
    assert retry_payload["read_window_remaining"] == 0
    assert retry_payload["read_daily_remaining"] == 258
    assert retry_payload["message"] == "Waiting for Strava read limits."


def test_process_summary_activity_uses_summary_data_without_detail_reads() -> None:
    client = SimpleNamespace(
        get_activity=MagicMock(side_effect=AssertionError("detail read should not happen")),
        get_activity_streams=MagicMock(side_effect=AssertionError("stream read should not happen")),
    )
    activity_summary = SimpleNamespace(
        id=123,
        elev_high=1344.7,
        start_date=datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc),
        name="Ben Lomond",
        sport_type="Hike",
    )
    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.tasks.strava.extract_activity_polyline",
            lambda _activity: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
        )
        monkeypatch.setattr(
            "app.tasks.strava._upsert_strava_activity",
            lambda **_kwargs: (SimpleNamespace(), True),
        )
        monkeypatch.setattr(
            "app.tasks.strava._upsert_user_bags_for_track",
            lambda **_kwargs: [{"munro_id": 1}],
        )
        monkeypatch.setattr("app.tasks.strava._mark_activity_status", lambda *_args, **_kwargs: None)

        result = _process_summary_activity(
            session=SimpleNamespace(),
            user=user,
            client=client,
            activity_summary=activity_summary,
            task=MagicMock(),
            result={"sync_mode": "latest"},
            quota_observer=StravaReadQuotaObserver(),
            sync_mode="latest",
        )

    assert result == {
        "status": "processed",
        "bag_rows_written": 1,
        "new_cached_activity": True,
    }
    client.get_activity.assert_not_called()
    client.get_activity_streams.assert_not_called()


def test_process_summary_activity_only_requests_missing_stream_types() -> None:
    get_activity_streams = MagicMock(
        return_value={"latlng": SimpleNamespace(data=[[56.1, -4.2], [56.2, -4.3]])}
    )
    client = SimpleNamespace(get_activity_streams=get_activity_streams)
    activity_summary = SimpleNamespace(
        id=456,
        elev_high=998.2,
        start_date=datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc),
        name="Lochside Loop",
        sport_type="Run",
    )
    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("app.tasks.strava.extract_activity_polyline", lambda _activity: None)
        monkeypatch.setattr(
            "app.tasks.strava._upsert_strava_activity",
            lambda **_kwargs: (SimpleNamespace(), False),
        )
        monkeypatch.setattr(
            "app.tasks.strava._upsert_user_bags_for_track",
            lambda **_kwargs: [],
        )
        monkeypatch.setattr("app.tasks.strava._mark_activity_status", lambda *_args, **_kwargs: None)

        result = _process_summary_activity(
            session=SimpleNamespace(),
            user=user,
            client=client,
            activity_summary=activity_summary,
            task=MagicMock(),
            result={"sync_mode": "full"},
            quota_observer=StravaReadQuotaObserver(),
            sync_mode="full",
        )

    assert result["status"] == "processed"
    get_activity_streams.assert_called_once_with(456, types=["latlng"], key_by_type=True)


def test_raise_for_retryable_rate_limit_requests_celery_retry() -> None:
    task = MagicMock()
    task.retry.side_effect = RuntimeError("retry invoked")
    response = SimpleNamespace(
        status_code=429,
        headers={
            "X-ReadRateLimit-Limit": "200,2000",
            "X-ReadRateLimit-Usage": "200,450",
        },
    )
    exc = SimpleNamespace(response=response)
    result = {
        "status": "in_progress",
        "user_id": "user-123",
        "sync_mode": "full",
        "activities_seen": 98,
        "activities_processed": 98,
        "activities_with_matches": 0,
        "activities_skipped_no_polyline": 9,
        "activities_skipped_invalid_polyline": 0,
        "activities_skipped_unverified_elevation": 0,
        "bag_rows_written": 0,
        "progress_percentage": 0.0,
        "progress_is_indeterminate": True,
        "sync_phase": "processing",
        "started_at": "2026-05-13T13:00:00+00:00",
        "updated_at": "2026-05-13T13:10:00+00:00",
        "message": "Checked 98 activities so far while syncing your full Strava history.",
    }

    with pytest.raises(RuntimeError, match="retry invoked"):
        _raise_for_retryable_rate_limit(
            task=task,
            exc=exc,  # type: ignore[arg-type]
            result=result,
            user_id="user-123",
            sync_mode="full",
            progress_is_indeterminate=True,
        )

    task.retry.assert_called_once()
    retry_kwargs = task.retry.call_args.kwargs
    assert isinstance(retry_kwargs["exc"], RuntimeError)
    retry_payload = json.loads(str(retry_kwargs["exc"]))
    assert retry_payload["message"] == "Waiting for Strava read limits."
    assert retry_payload["sync_mode"] == "full"
    assert retry_payload["sync_phase"] == "waiting_for_rate_limit"
    assert retry_payload["rate_limit_scope"] == "short_window"
    assert retry_payload["activities_seen"] == 98
    assert retry_payload["activities_processed"] == 98
    assert retry_payload["read_window_limit"] == 200
    assert retry_payload["read_window_usage"] == 200
    assert retry_payload["read_window_remaining"] == 0
    assert retry_payload["read_daily_limit"] == 2000
    assert retry_payload["read_daily_usage"] == 450
    assert retry_payload["read_daily_remaining"] == 1550
    assert retry_payload["read_window_resets_at"]
    assert retry_payload["read_daily_resets_at"]
    assert retry_payload["rate_limit_wait_seconds"] >= 60
    assert retry_payload["estimated_remaining_seconds"] >= retry_payload["rate_limit_wait_seconds"]
    assert retry_payload["retry_after"]
    assert retry_kwargs["countdown"] >= 60
