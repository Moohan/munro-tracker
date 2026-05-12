# MunroStream

MunroStream is a local-first MVP for Scottish hill bagging. It syncs Strava activities, processes summit matches asynchronously with Celery and Redis, stores Munro data in PostGIS, and presents bagging progress on a React + Leaflet dashboard.

## What is implemented

- DoBIH-backed Munro seeding with `Munro Top` rows excluded and alternative names preserved
- Strava OAuth callback handling with refresh-token-safe persistence
- Strava webhook verification and idempotent webhook event ingestion
- Async activity processing that:
  - decodes Strava polylines in the worker
  - applies `ST_DWithin(...::geography, 100)` bagging checks against `geometry(Point, 4326)` summit data
  - requires a recorded high point of at least `summit height - 20 metres`
- Dashboard stats for total Munros, bagged Munros, completion percentage, total ascent, and last bag date
- Leaflet frontend with real OS Maps support when configured, and OpenTopoMap/OpenStreetMap fallbacks when not

## Quick start

1. Copy `.env.example` to `.env`.
2. Start the stack:

```bash
docker compose up --build
```

The local stack now waits for a one-shot DoBIH seed step before the API starts, so the public Munro list is populated on first launch. If Strava OAuth is not configured locally, the app still loads and shows a disabled Strava control with a setup message.

3. Open:

- API docs: `http://localhost:18000/docs`
- Frontend: `http://localhost:5173`

## Environment

Backend:

- `STRAVA_CLIENT_ID`
- `STRAVA_CLIENT_SECRET`
- `STRAVA_REDIRECT_URI`
- `STRAVA_WEBHOOK_VERIFY_TOKEN`
- `STRAVA_WEBHOOK_SECRET`

Frontend:

- `VITE_OS_MAPS_API_KEY`
- `VITE_OS_MAPS_TILE_URL_TEMPLATE`
- `VITE_OS_MAPS_ATTRIBUTION`

`STRAVA_WEBHOOK_VERIFY_TOKEN` is the canonical verification token. `STRAVA_WEBHOOK_SECRET` is still accepted as a fallback so older local setups do not break immediately.

## Local commands

Backend:

```bash
cd backend
pip install -r requirements.txt
python3 -m pytest -q
```

Frontend:

```bash
cd frontend
npm install
npm test
npm run build
```

DoBIH verification / seed:

```bash
python3 backend/scripts/seed_munros.py --verify-only
python3 backend/scripts/seed_munros.py
```

## Architecture notes

- All coordinates remain `SRID 4326`.
- All metric distance checks cast to geography and use a hard `100` metre threshold.
- The FastAPI layer stays stateless; expensive activity analysis runs only in Celery workers.
- Dashboard totals use persisted activity records rather than client-side guesses.
- UI copy uses British English and all displayed measurements are metric.

## Known local setup note

The schema is still bootstrapped from `infra/db/init/001_init.sql`. If you already have an older local Postgres volume, recreate it after pulling schema changes so the new Strava activity and webhook tables are created cleanly.
