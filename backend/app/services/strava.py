from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from stravalib.client import Client

from app.core.config import get_settings
from app.infrastructure.runtime_schema import ensure_strava_sync_metadata_schema
from app.models import User

REQUESTED_STRAVA_SCOPES = ("read", "activity:read_all")
REQUIRED_STRAVA_SCOPES = frozenset({"activity:read_all"})
STRAVA_CLIENT_ID_SETTING_KEY = "strava_client_id"
STRAVA_CLIENT_SECRET_SETTING_KEY = "strava_client_secret"
APP_SETTINGS_TABLE_SQL = text(
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """
)
APP_SETTING_UPSERT_SQL = text(
    """
    INSERT INTO app_settings (key, value)
    VALUES (:key, :value)
    ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value,
        updated_at = NOW()
    """
)
APP_SETTING_SELECT_SQL = text("SELECT value FROM app_settings WHERE key = :key")


RateLimitObserver = Callable[[dict[str, str], str], None]


@dataclass(frozen=True)
class StravaOAuthRuntimeConfig:
    client_id: int
    client_secret: str


def parse_scope_string(scope: str | None) -> set[str]:
    return {part.strip() for part in (scope or "").split(",") if part.strip()}


def ensure_required_scopes(accepted_scopes: Iterable[str]) -> None:
    accepted = {scope.strip() for scope in accepted_scopes if scope.strip()}
    missing = REQUIRED_STRAVA_SCOPES.difference(accepted)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(
            f"Strava authorisation is missing required scope(s): {missing_list}."
        )


def validate_frontend_redirect_url(next_url: str | None) -> str | None:
    if not next_url:
        return None

    settings = get_settings()
    frontend_origin = settings.frontend_origin.rstrip("/")
    if not frontend_origin:
        raise ValueError("FRONTEND_ORIGIN is not configured.")

    parsed_frontend = urlparse(frontend_origin)
    candidate = next_url.strip()
    parsed_candidate = urlparse(candidate)

    if not parsed_candidate.scheme and not parsed_candidate.netloc:
        return urljoin(f"{frontend_origin}/", candidate.lstrip("/"))

    if (
        parsed_candidate.scheme != parsed_frontend.scheme
        or parsed_candidate.netloc != parsed_frontend.netloc
    ):
        raise ValueError("next_url must point to the configured frontend origin.")

    return candidate


def get_strava_redirect_uri(request: Request) -> str:
    settings = get_settings()
    return settings.strava_redirect_uri or str(request.url_for("strava_oauth_callback"))


def get_strava_webhook_verify_token() -> str:
    settings = get_settings()
    token = (
        (settings.strava_webhook_verify_token or "").strip()
        or settings.strava_webhook_secret.strip()
    )
    if not token or token == "replace-me":
        raise ValueError("STRAVA_WEBHOOK_VERIFY_TOKEN is not configured.")
    return token


def get_strava_oauth_status(
    session: Session | None = None,
) -> dict[str, bool | str | None]:
    oauth_config = get_strava_oauth_runtime_config(session)
    client_id_missing = oauth_config.client_id <= 0
    client_secret_missing = _is_missing_client_secret(oauth_config.client_secret)

    if client_id_missing and client_secret_missing:
        return {
            "oauth_available": False,
            "reason": "missing_client_id",
            "message": (
                "Strava connection is not available in this local setup yet. "
                "Add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to enable it."
            ),
        }

    if client_id_missing:
        return {
            "oauth_available": False,
            "reason": "missing_client_id",
            "message": (
                "Strava connection is not available in this local setup yet. "
                "Add STRAVA_CLIENT_ID to enable it."
            ),
        }

    if client_secret_missing:
        return {
            "oauth_available": False,
            "reason": "missing_client_secret",
            "message": (
                "Strava connection is not available in this local setup yet. "
                "Add STRAVA_CLIENT_SECRET to enable it."
            ),
        }

    return {
        "oauth_available": True,
        "reason": None,
        "message": "Strava OAuth is available.",
    }


def create_oauth_state(
    next_url: str | None = None,
    session: Session | None = None,
) -> str:
    payload = {
        "iat": int(time.time()),
        "next_url": validate_frontend_redirect_url(next_url),
    }
    payload_bytes = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = _urlsafe_b64encode(payload_bytes)
    signature = _sign_state(encoded_payload, session)
    return f"{encoded_payload}.{signature}"


def parse_oauth_state(
    state: str,
    session: Session | None = None,
) -> dict[str, Any]:
    try:
        encoded_payload, signature = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid Strava state parameter.") from exc

    expected_signature = _sign_state(encoded_payload, session)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Invalid Strava state signature.")

    try:
        payload = json.loads(_urlsafe_b64decode(encoded_payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Strava state payload.") from exc

    issued_at = int(payload.get("iat") or 0)
    if issued_at <= 0:
        raise ValueError("Invalid Strava state timestamp.")

    settings = get_settings()
    if time.time() - issued_at > settings.strava_oauth_state_ttl_seconds:
        raise ValueError("Strava authorisation state has expired.")

    return {
        "iat": issued_at,
        "next_url": validate_frontend_redirect_url(payload.get("next_url")),
    }


def build_authorization_url(
    *,
    redirect_uri: str,
    state: str,
    approval_prompt: str = "auto",
    session: Session | None = None,
) -> str:
    oauth_config = get_strava_oauth_runtime_config(session)
    if oauth_config.client_id <= 0:
        raise ValueError("STRAVA_CLIENT_ID is not configured.")

    client = Client()
    return client.authorization_url(
        client_id=oauth_config.client_id,
        redirect_uri=redirect_uri,
        approval_prompt=approval_prompt,
        scope=list(REQUESTED_STRAVA_SCOPES),
        state=state,
    )


def exchange_code_for_token(
    code: str,
    session: Session | None = None,
) -> dict[str, Any]:
    client = Client()
    oauth_config = get_strava_oauth_runtime_config(session)
    exchange_result = client.exchange_code_for_token(
        client_id=oauth_config.client_id,
        client_secret=_require_strava_client_secret(session),
        code=code,
        return_athlete=True,
    )
    token_bundle, athlete = _normalise_token_exchange_result(exchange_result)

    if athlete:
        token_bundle["athlete"] = athlete
        return token_bundle

    access_token = str(token_bundle.get("access_token") or "").strip()
    if access_token:
        authenticated_client = build_authenticated_client(access_token)
        token_bundle["athlete"] = _normalise_mapping(
            authenticated_client.get_athlete()
        )

    return token_bundle


def build_authenticated_client(
    access_token: str,
    *,
    rate_limit_observer: RateLimitObserver | None = None,
) -> Client:
    if rate_limit_observer is not None:
        return Client(
            access_token=access_token,
            rate_limiter=rate_limit_observer,
        )

    return Client(
        access_token=access_token,
        rate_limit_requests=False,
    )


def ensure_fresh_access_token(
    session: Session,
    user: User,
    *,
    force_refresh: bool = False,
) -> tuple[str, bool]:
    now = datetime.now(timezone.utc)
    token_expires_at = _ensure_utc_datetime(user.token_expires_at)

    if (
        not force_refresh
        and user.access_token
        and token_expires_at > now + timedelta(minutes=5)
    ):
        return user.access_token, False

    client = Client()
    oauth_config = get_strava_oauth_runtime_config(session)
    token_bundle = client.refresh_access_token(
        client_id=oauth_config.client_id,
        client_secret=_require_strava_client_secret(session),
        refresh_token=user.refresh_token,
    )
    _apply_token_bundle(user, _normalise_mapping(token_bundle))
    session.add(user)
    session.flush()

    if not user.access_token:
        raise ValueError("Strava token refresh did not return an access token.")

    return user.access_token, True


def upsert_user_from_auth(session: Session, token_bundle: Mapping[str, Any]) -> User:
    ensure_strava_sync_metadata_schema(session)
    athlete = _normalise_mapping(token_bundle.get("athlete"))
    athlete_id = athlete.get("id")
    if athlete_id is None:
        raise ValueError("Strava token exchange did not return an athlete id.")

    strava_athlete_id = int(athlete_id)
    user = session.scalar(
        select(User).where(User.strava_athlete_id == strava_athlete_id)
    )
    if user is None:
        user = User(
            strava_athlete_id=strava_athlete_id,
            refresh_token="pending",
            token_expires_at=_token_expires_at(token_bundle),
        )
        session.add(user)

    _apply_token_bundle(user, token_bundle)
    user.display_name = _build_display_name(athlete)
    user.profile_image_url = _first_non_empty(
        athlete.get("profile_medium"),
        athlete.get("profile"),
    )
    session.flush()
    return user


def extract_activity_polyline(activity: Any) -> str | None:
    map_payload = getattr(activity, "map", None)
    if map_payload is None:
        return None

    polyline = _first_non_empty(
        getattr(map_payload, "polyline", None),
        getattr(map_payload, "summary_polyline", None),
    )
    return polyline or None


def get_strava_oauth_runtime_config(
    session: Session | None = None,
) -> StravaOAuthRuntimeConfig:
    settings = get_settings()
    client_id = settings.strava_client_id
    client_secret = settings.strava_client_secret.strip()

    if session is not None:
        _ensure_app_settings_table(session)
        stored_client_id = _get_app_setting_value(session, STRAVA_CLIENT_ID_SETTING_KEY)
        stored_client_secret = _get_app_setting_value(
            session,
            STRAVA_CLIENT_SECRET_SETTING_KEY,
        )

        if stored_client_id:
            try:
                client_id = int(stored_client_id)
            except ValueError:
                client_id = 0

        if stored_client_secret:
            client_secret = stored_client_secret.strip()

    return StravaOAuthRuntimeConfig(
        client_id=client_id,
        client_secret=client_secret,
    )


def save_strava_oauth_settings(
    session: Session,
    *,
    client_id: int,
    client_secret: str,
) -> None:
    normalised_secret = client_secret.strip()
    if client_id <= 0:
        raise ValueError("STRAVA_CLIENT_ID must be a positive integer.")
    if _is_missing_client_secret(normalised_secret):
        raise ValueError("STRAVA_CLIENT_SECRET must not be empty.")

    _ensure_app_settings_table(session)
    session.execute(
        APP_SETTING_UPSERT_SQL,
        {
            "key": STRAVA_CLIENT_ID_SETTING_KEY,
            "value": str(client_id),
        },
    )
    session.execute(
        APP_SETTING_UPSERT_SQL,
        {
            "key": STRAVA_CLIENT_SECRET_SETTING_KEY,
            "value": normalised_secret,
        },
    )


def _apply_token_bundle(user: User, token_bundle: Mapping[str, Any]) -> None:
    refresh_token = str(token_bundle.get("refresh_token") or "").strip()
    access_token = str(token_bundle.get("access_token") or "").strip()
    if not refresh_token or not access_token:
        raise ValueError("Strava token bundle is missing token fields.")

    user.refresh_token = refresh_token
    user.access_token = access_token
    user.token_expires_at = _token_expires_at(token_bundle)


def _token_expires_at(token_bundle: Mapping[str, Any]) -> datetime:
    raw_expires_at = token_bundle.get("expires_at")
    if raw_expires_at is None:
        raise ValueError("Strava token bundle is missing expires_at.")

    return datetime.fromtimestamp(int(raw_expires_at), tz=timezone.utc)


def _build_display_name(athlete: Mapping[str, Any]) -> str | None:
    full_name = " ".join(
        part.strip()
        for part in [
            str(athlete.get("firstname") or ""),
            str(athlete.get("lastname") or ""),
        ]
        if part.strip()
    )
    return full_name or _first_non_empty(athlete.get("username"))


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        candidate = str(value).strip()
        if candidate:
            return candidate
    return None


def _normalise_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    raise ValueError("Unexpected Strava response payload.")


def _normalise_token_exchange_result(
    value: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("Unexpected Strava token exchange payload.")
        token_bundle = _normalise_mapping(value[0])
        athlete = _normalise_mapping(value[1])
        return token_bundle, athlete

    token_bundle = _normalise_mapping(value)
    athlete = _normalise_mapping(token_bundle.get("athlete"))
    return token_bundle, athlete


def _ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_strava_client_secret(session: Session | None = None) -> str:
    oauth_config = get_strava_oauth_runtime_config(session)
    client_secret = oauth_config.client_secret.strip()
    if _is_missing_client_secret(client_secret):
        raise ValueError("STRAVA_CLIENT_SECRET is not configured.")
    if oauth_config.client_id <= 0:
        raise ValueError("STRAVA_CLIENT_ID is not configured.")
    return client_secret


def _sign_state(
    encoded_payload: str,
    session: Session | None = None,
) -> str:
    signature = hmac.new(
        _require_strava_client_secret(session).encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _urlsafe_b64encode(signature)


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _is_missing_client_secret(value: str) -> bool:
    return not value.strip() or value.strip() == "replace-me"


def _ensure_app_settings_table(session: Session) -> None:
    session.execute(APP_SETTINGS_TABLE_SQL)


def _get_app_setting_value(session: Session, key: str) -> str | None:
    result = session.execute(
        APP_SETTING_SELECT_SQL,
        {"key": key},
    ).scalar_one_or_none()
    if result is None:
        return None
    return str(result)
