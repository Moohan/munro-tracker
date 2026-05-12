# MunroStream Architecture Review

## Summary

The repo now implements the MVP architecture described in the original spec:

- FastAPI remains a stateless orchestration layer
- Celery + Redis own heavy Strava activity processing
- PostGIS stores Munros, webhook receipts, and processed Strava activity evidence
- React + Leaflet provide the bagging dashboard with optional OS Maps tiles

## Implemented flow

1. The frontend redirects the user to `/api/v1/strava/oauth/login`.
2. The Strava callback exchanges the code, persists the refresh token, access token, and expiry timestamp, then queues a background sync.
3. Strava webhooks are verified through `GET /api/v1/strava/webhook` and received through `POST /api/v1/strava/webhook`.
4. Webhook payloads are stored in `strava_webhook_events` using an idempotent event key so duplicate deliveries do not re-enqueue duplicate work.
5. The worker fetches detailed activity data, stores it in `strava_activities`, decodes the polyline, extracts a usable high point, and only bags a Munro when:
   - the track comes within 100 metres of the summit using `ST_DWithin(...::geography, 100)`
   - the activity high point is at least `munro.height_metres - 20`
6. Dashboard stats are served from persisted `user_bags` and `strava_activities` data.

## Data model notes

- `munros.geom` remains `geometry(Point, 4326)`.
- `strava_activities` stores per-user Strava activity metadata, elevation gain, high point, summary polyline, worker status, and errors.
- `strava_webhook_events` stores raw webhook payloads, routing metadata, queue state, and an idempotency key.
- `user_bags` still records the earliest bagging timestamp and best matched summit distance per user/Munro pair.

## Frontend behaviour

- The frontend stores the connected user ID in local browser storage rather than creating a server session.
- Dashboard progress, ascent totals, and last-bag date come from the backend dashboard endpoint.
- OS Maps tiles are used when `VITE_OS_MAPS_API_KEY` is configured; otherwise the UI falls back to OpenTopoMap and OpenStreetMap.

## Remaining future work

- Activity heatmaps are still out of scope for this MVP pass.
- There is not yet a manual review workflow for borderline activities or deleted Strava activities.
- The schema still relies on bootstrap SQL rather than incremental migrations.
