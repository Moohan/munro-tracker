from functools import lru_cache

from sqlalchemy.orm import Session

from app.infrastructure.session import get_engine

RUNTIME_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS user_bag_activities (
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        munro_id BIGINT NOT NULL REFERENCES munros(id) ON DELETE CASCADE,
        source_activity_id BIGINT NOT NULL,
        bagged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        matched_distance_metres NUMERIC(6, 2) CHECK (
            matched_distance_metres IS NULL
            OR (matched_distance_metres >= 0 AND matched_distance_metres <= 100.00)
        ),
        summit_elevation_metres NUMERIC(7, 2),
        source TEXT NOT NULL DEFAULT 'strava',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, munro_id, source_activity_id)
    );
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_user_bag_activities_munro_history
        ON user_bag_activities (user_id, munro_id, bagged_at DESC);
    """.strip(),
)

STRAVA_SYNC_METADATA_SCHEMA_STATEMENTS = (
    """
    ALTER TABLE users
        ADD COLUMN IF NOT EXISTS strava_activity_total_count BIGINT;
    """.strip(),
    """
    ALTER TABLE users
        ADD COLUMN IF NOT EXISTS strava_activity_total_refreshed_at TIMESTAMPTZ;
    """.strip(),
)


def _run_schema_statements(
    statements: tuple[str, ...],
    session: Session | None = None,
) -> None:
    if session is not None:
        connection = session.connection()
        for statement in statements:
            connection.exec_driver_sql(statement)
        return

    engine = get_engine()
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


@lru_cache(maxsize=1)
def _ensure_repeat_bag_schema_cached() -> None:
    _run_schema_statements(RUNTIME_SCHEMA_STATEMENTS)


def ensure_repeat_bag_schema(session: Session | None = None) -> None:
    if session is not None:
        _run_schema_statements(RUNTIME_SCHEMA_STATEMENTS, session)
        return
    _ensure_repeat_bag_schema_cached()


@lru_cache(maxsize=1)
def _ensure_strava_sync_metadata_schema_cached() -> None:
    _run_schema_statements(STRAVA_SYNC_METADATA_SCHEMA_STATEMENTS)


def ensure_strava_sync_metadata_schema(session: Session | None = None) -> None:
    if session is not None:
        _run_schema_statements(STRAVA_SYNC_METADATA_SCHEMA_STATEMENTS, session)
        return
    _ensure_strava_sync_metadata_schema_cached()
