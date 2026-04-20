from typing import Any

from sqlalchemy.orm import Session

from app.models import User
from app.services.strava import (
    ensure_required_scopes,
    exchange_code_for_token,
    parse_oauth_state,
    parse_scope_string,
    upsert_user_from_auth,
)
from app.tasks.strava import sync_latest_activities_for_user


def onboard_user_from_strava_callback(
    db: Session,
    code: str,
    state: str,
    scope: str,
) -> dict[str, Any]:
    state_payload = parse_oauth_state(state)
    accepted_scopes = sorted(parse_scope_string(scope))
    ensure_required_scopes(accepted_scopes)

    token_bundle = exchange_code_for_token(code)
    user = upsert_user_from_auth(db, token_bundle)
    db.commit()
    db.refresh(user)

    sync_enqueued = False
    sync_task_id: str | None = None
    try:
        from celery.exceptions import CeleryError

        task_result = sync_latest_activities_for_user.delay(str(user.id))
        sync_task_id = task_result.id
        sync_enqueued = True
    except CeleryError:
        sync_enqueued = False

    redirect_to = state_payload.get("next_url")
    response_payload = {
        "user_id": user.id,
        "strava_athlete_id": user.strava_athlete_id,
        "display_name": user.display_name,
        "accepted_scopes": accepted_scopes,
        "sync_enqueued": sync_enqueued,
        "sync_task_id": sync_task_id,
        "redirect_to": redirect_to,
    }

    return response_payload
