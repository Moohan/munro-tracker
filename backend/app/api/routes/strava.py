from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from celery.exceptions import CeleryError
from fastapi.responses import RedirectResponse
from requests.exceptions import RequestException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.session import get_db
from app.models import User
from app.schemas.strava import StravaConnectionResponse, StravaSyncQueuedResponse
from app.services.strava_onboarding import onboard_user_from_strava_callback
from app.tasks.strava import sync_latest_activities_for_user

router = APIRouter()


@router.get("/oauth/login", name="strava_oauth_login")
def strava_oauth_login(
    request: Request,
    next_url: str | None = Query(default=None),
    approval_prompt: Literal["auto", "force"] = Query(default="auto"),
) -> RedirectResponse:
    try:
        validated_next_url = validate_frontend_redirect_url(next_url)
        state = create_oauth_state(validated_next_url)
        authorisation_url = build_authorization_url(
            redirect_uri=get_strava_redirect_uri(request),
            state=state,
            approval_prompt=approval_prompt,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return RedirectResponse(authorisation_url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/oauth/callback",
    name="strava_oauth_callback",
    response_model=StravaConnectionResponse,
)
def strava_oauth_callback(
    code: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> StravaConnectionResponse | RedirectResponse:
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Strava authorisation failed: {error}.",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Strava authorisation callback parameters.",
        )

    try:
        response_payload = onboard_user_from_strava_callback(db, code, state, scope)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (RequestException, SQLAlchemyError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Strava token exchange or database operation failed.",
        ) from exc

    response = StravaConnectionResponse(**response_payload)

    if response.redirect_to:
        redirect_url = _append_query_params(
            response.redirect_to,
            {
                "status": "connected",
                "user_id": str(response.user_id),
                "strava_athlete_id": str(response.strava_athlete_id),
                "sync_enqueued": str(response.sync_enqueued).lower(),
                "sync_task_id": response.sync_task_id,
            },
        )
        return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)

    return response


@router.post(
    "/users/{user_id}/sync",
    response_model=StravaSyncQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_strava_sync(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> StravaSyncQueuedResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    try:
        task_result = sync_latest_activities_for_user.delay(str(user.id))
    except CeleryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue a Strava sync right now.",
        ) from exc

    return StravaSyncQueuedResponse(
        user_id=user.id,
        task_id=task_result.id,
        activity_limit=get_settings().strava_sync_activity_limit,
    )


def _append_query_params(url: str, params: dict[str, str | None]) -> str:
    parts = urlsplit(url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))
    query_params.update({key: value for key, value in params.items() if value is not None})
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_params),
            parts.fragment,
        )
    )
