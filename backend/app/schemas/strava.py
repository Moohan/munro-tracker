import uuid
from typing import Literal

from pydantic import BaseModel


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
    activity_limit: int


class StravaOAuthStatusResponse(BaseModel):
    oauth_available: bool
    reason: Literal["missing_client_id", "missing_client_secret"] | None = None
    message: str
