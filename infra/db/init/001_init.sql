CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strava_athlete_id BIGINT NOT NULL UNIQUE,
    display_name TEXT,
    profile_image_url TEXT,
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    token_expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_user_token_safety
        CHECK (access_token IS NULL OR (refresh_token IS NOT NULL AND token_expires_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS munros (
    id BIGSERIAL PRIMARY KEY,
    dobih_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    alternative_names TEXT[] NOT NULL DEFAULT '{}',
    hill_category TEXT NOT NULL DEFAULT 'MUN',
    is_munro_top BOOLEAN NOT NULL DEFAULT FALSE,
    height_metres NUMERIC(6, 1) NOT NULL CHECK (height_metres > 0),
    prominence_metres NUMERIC(6, 1),
    area TEXT,
    geom GEOMETRY(POINT, 4326) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_munros_exclude_tops CHECK (is_munro_top = FALSE)
);

CREATE INDEX IF NOT EXISTS idx_munros_geom ON munros USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_munros_geography ON munros USING GIST ((geom::geography));

CREATE TABLE IF NOT EXISTS user_bags (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    munro_id BIGINT NOT NULL REFERENCES munros(id) ON DELETE CASCADE,
    source_activity_id BIGINT,
    bagged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    matched_distance_metres NUMERIC(6, 2) CHECK (
        matched_distance_metres IS NULL
        OR (matched_distance_metres >= 0 AND matched_distance_metres <= 100.00)
    ),
    summit_elevation_metres NUMERIC(7, 2),
    source TEXT NOT NULL DEFAULT 'strava',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, munro_id)
);

CREATE INDEX IF NOT EXISTS idx_user_bags_bagged_at ON user_bags (bagged_at DESC);

COMMENT ON COLUMN munros.geom IS
    'Summit coordinate stored as geometry(Point, 4326); cast to geography for metric ST_DWithin bagging checks.';

COMMENT ON TABLE user_bags IS
    'Join table recording when a user has bagged a Munro, including spatial evidence from Strava activity processing.';
