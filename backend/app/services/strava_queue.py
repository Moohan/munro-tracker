from __future__ import annotations

import uuid
from typing import Literal

from celery.result import AsyncResult

from app.tasks.strava import (
    refresh_activity_totals_for_user,
    sync_activity_for_user,
    sync_all_activities_for_user,
    sync_latest_activities_for_user,
)

ManualSyncMode = Literal["latest", "full", "totals"]


def enqueue_latest_activities_sync(user_id: uuid.UUID | str) -> AsyncResult:
    return sync_latest_activities_for_user.apply_async(args=[str(user_id)])


def enqueue_full_activities_sync(user_id: uuid.UUID | str) -> AsyncResult:
    return sync_all_activities_for_user.apply_async(args=[str(user_id)])


def enqueue_activity_totals_refresh(user_id: uuid.UUID | str) -> AsyncResult:
    return refresh_activity_totals_for_user.apply_async(args=[str(user_id)])


def enqueue_activity_sync(user_id: uuid.UUID | str, activity_id: int) -> AsyncResult:
    return sync_activity_for_user.apply_async(args=[str(user_id), int(activity_id)])
