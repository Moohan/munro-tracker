import uuid
from typing import Literal

from pydantic import BaseModel, Field


class StravaConnectionResponse(BaseModel):
    user_id: uuid.UUID
    strava_athlete_id: int
    display_name: str | None
    accepted_scopes: list[str]
    sync_enqueued: bool
    sync_task_id: str | None = None
    redirect_to: str | None = None


class StravaSyncQueuedResponse(BaseModel):
    user_id: uuid.UUID
    task_id: str
    activity_limit: int | None
    sync_mode: Literal["latest", "full", "totals"]


class StravaSyncTaskStatusResponse(BaseModel):
    task_id: str
    state: str
    message: str
    sync_mode: Literal["latest", "full", "activity", "totals"] | None = None
    activities_seen: int = 0
    activities_processed: int = 0
    activities_with_matches: int = 0
    activities_skipped_no_polyline: int = 0
    activities_skipped_invalid_polyline: int = 0
    activities_skipped_unverified_elevation: int = 0
    bag_rows_written: int = 0
    cached_strava_activities: int | None = None
    total_strava_activities: int | None = None
    progress_percentage: float = 0
    progress_is_indeterminate: bool = False
    sync_phase: Literal[
        "pending",
        "preparing",
        "discovering",
        "downloading",
        "processing",
        "waiting_for_rate_limit",
        "complete",
        "failed",
    ] | None = None
    started_at: str | None = None
    updated_at: str | None = None
    estimated_remaining_seconds: int | None = None
    rate_limit_wait_seconds: int | None = None
    rate_limit_scope: Literal["short_window", "daily"] | None = None
    read_window_limit: int | None = None
    read_window_usage: int | None = None
    read_window_remaining: int | None = None
    read_daily_limit: int | None = None
    read_daily_usage: int | None = None
    read_daily_remaining: int | None = None
    read_window_resets_at: str | None = None
    read_daily_resets_at: str | None = None
    retry_after: str | None = None
    is_complete: bool = False
    is_error: bool = False


class StravaOAuthStatusResponse(BaseModel):
    oauth_available: bool
    reason: Literal["missing_client_id", "missing_client_secret"] | None = None
    message: str


class StravaOAuthSettingsUpdateRequest(BaseModel):
    client_id: int = Field(gt=0)
    client_secret: str = Field(min_length=1)
