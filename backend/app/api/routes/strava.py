from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import uuid
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from celery.exceptions import CeleryError
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from requests.exceptions import RequestException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.session import get_db
from app.models import StravaWebhookEvent, User
from app.schemas.strava import (
    StravaConnectionResponse,
    StravaOAuthStatusResponse,
    StravaSyncQueuedResponse,
)
from app.services.strava import (
    build_authorization_url,
    create_oauth_state,
    get_strava_oauth_status,
    get_strava_redirect_uri,
    get_strava_webhook_verify_token,
    parse_oauth_state,
    validate_frontend_redirect_url,
)
from app.services.strava_onboarding import onboard_user_from_strava_callback
from app.tasks.strava import sync_activity_for_user, sync_latest_activities_for_user

router = APIRouter()


@router.get("/oauth/status", response_model=StravaOAuthStatusResponse)
def strava_oauth_status() -> StravaOAuthStatusResponse:
    return StravaOAuthStatusResponse(**get_strava_oauth_status())


@router.get("/oauth/login", name="strava_oauth_login")
def strava_oauth_login(
    request: Request,
    next_url: str | None = Query(default=None),
    approval_prompt: Literal["auto", "force"] = Query(default="auto"),
) -> RedirectResponse:
    try:
        validated_next_url = validate_frontend_redirect_url(next_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    oauth_status = get_strava_oauth_status()
    if not oauth_status["oauth_available"]:
        if validated_next_url:
            return _redirect_to_frontend_error(
                validated_next_url,
                str(oauth_status["reason"] or "oauth_unavailable"),
                str(oauth_status["message"]),
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(oauth_status["message"]),
        )

    try:
        state = create_oauth_state(validated_next_url)
        authorisation_url = build_authorization_url(
            redirect_uri=get_strava_redirect_uri(request),
            state=state,
            approval_prompt=approval_prompt,
        )
    except ValueError as exc:
        if validated_next_url:
            return _redirect_to_frontend_error(
                validated_next_url,
                _map_oauth_error_code(str(exc)),
                str(exc),
            )
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
        next_url = _recover_trusted_frontend_redirect(state)
        error_message = f"Strava authorisation failed: {error}."
        if next_url:
            return _redirect_to_frontend_error(
                next_url,
                "authorisation_failed",
                error_message,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message,
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
                "display_name": response.display_name,
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


@router.get("/webhook")
def verify_strava_webhook_subscription(
    mode: str = Query(alias="hub.mode"),
    verify_token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> dict[str, str]:
    if mode != "subscribe":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported Strava webhook mode.",
        )

    try:
        expected_token = get_strava_webhook_verify_token()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if verify_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Strava webhook verify token.",
        )

    return {"hub.challenge": challenge}


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
def receive_strava_webhook_event(
    payload: dict[str, object] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    event = _normalise_webhook_payload(payload)
    user = db.scalar(select(User).where(User.strava_athlete_id == event["owner_id"]))
    event_key = _build_event_key(event)

    webhook_event = StravaWebhookEvent(
        event_key=event_key,
        user_id=user.id if user else None,
        owner_id=event["owner_id"],
        object_id=event["object_id"],
        object_type=event["object_type"],
        aspect_type=event["aspect_type"],
        event_time=datetime.fromtimestamp(event["event_time"], tz=timezone.utc),
        subscription_id=event["subscription_id"],
        updates=event["updates"],
        payload=event["payload"],
        processing_status="received",
    )
    db.add(webhook_event)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate", "task_id": None}

    task_id: str | None = None
    if user is None:
        webhook_event.processing_status = "ignored_unknown_user"
    elif event["object_type"] != "activity" or event["aspect_type"] not in {"create", "update"}:
        webhook_event.processing_status = "ignored_event_type"
    else:
        try:
            task_result = sync_activity_for_user.delay(str(user.id), int(event["object_id"]))
        except CeleryError as exc:
            webhook_event.processing_status = "enqueue_failed"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to queue the Strava webhook activity right now.",
            ) from exc

        task_id = task_result.id
        webhook_event.enqueued_task_id = task_id
        webhook_event.processing_status = "enqueued"

    db.commit()
    return {"status": webhook_event.processing_status, "task_id": task_id}


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


def _redirect_to_frontend_error(
    next_url: str,
    error_code: str,
    error_message: str,
) -> RedirectResponse:
    redirect_url = _append_query_params(
        next_url,
        {
            "status": "error",
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)


def _recover_trusted_frontend_redirect(state: str | None) -> str | None:
    if not state:
        return None

    try:
        payload = parse_oauth_state(state)
    except ValueError:
        return None

    return payload.get("next_url")


def _map_oauth_error_code(message: str) -> str:
    if "STRAVA_CLIENT_ID" in message:
        return "missing_client_id"
    if "STRAVA_CLIENT_SECRET" in message:
        return "missing_client_secret"
    return "oauth_unavailable"


def _normalise_webhook_payload(payload: dict[str, object]) -> dict[str, object]:
    try:
        owner_id = int(payload["owner_id"])
        object_id = int(payload["object_id"])
        object_type = str(payload["object_type"]).strip()
        aspect_type = str(payload["aspect_type"]).strip()
        event_time = int(payload["event_time"])
        subscription_id = payload.get("subscription_id")
        if subscription_id is not None:
            subscription_id = int(subscription_id)
        updates = payload.get("updates") or {}
        if not isinstance(updates, dict):
            updates = {}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Strava webhook payload.",
        ) from exc

    return {
        "owner_id": owner_id,
        "object_id": object_id,
        "object_type": object_type,
        "aspect_type": aspect_type,
        "event_time": event_time,
        "subscription_id": subscription_id,
        "updates": updates,
        "payload": payload,
    }


def _build_event_key(event: dict[str, object]) -> str:
    raw_key = "|".join(
        [
            str(event["owner_id"]),
            str(event["object_id"]),
            str(event["object_type"]),
            str(event["aspect_type"]),
            str(event["event_time"]),
        ]
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
