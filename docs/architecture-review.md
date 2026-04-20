# MunroStream Architecture Review

## Summary

The project specification is coherent and well-scoped for an incremental delivery, but several implementation details needed to be made explicit in the scaffold:

- Spatial matching should use summit proximity rather than polygon containment for the first release, because the source dataset naturally provides summit coordinates.
- The API should not decode polylines or run expensive spatial joins inline; those jobs belong in Celery workers.
- OAuth persistence needs defensive schema constraints so Strava token refresh can be implemented without retrofitting storage guarantees later.

## Recommended Baseline Architecture

- `frontend`: React + Vite with Tailwind CSS and API proxying during local development.
- `api`: FastAPI serving stateless HTTP endpoints for auth, dashboard, and activity orchestration.
- `worker`: Celery process for GPX/polyline decoding, elevation verification, and `ST_DWithin` summit matching.
- `db`: PostgreSQL 16 + PostGIS storing users, Munros, and bag evidence.
- `redis`: broker/result backend for asynchronous work.

## Data Design Notes

- `munros.geom` is stored as `geometry(Point, 4326)` for source fidelity and PostGIS interoperability.
- Distance-based queries should cast `geom` to geography to respect the 100 metre threshold accurately:

```sql
ST_DWithin(track.geom::geography, munros.geom::geography, 100)
```

- `munros.is_munro_top` is retained as a guardrail column, but the schema rejects `true` values so accidental Munro Top ingestion fails loudly.
- `user_bags` is modelled as a join table with evidence columns, allowing later expansion into manual review and reprocessing workflows.

## Immediate Next Steps

1. Add an ingestion command that loads DoBIH CSV rows, maps alternative names, and rejects Munro Tops before insert.
2. Implement Strava OAuth callback and refresh token rotation with encrypted secret storage if production deployment is planned.
3. Add an activities table plus webhook idempotency handling before turning on Strava subscriptions.
4. Replace the worker stub with real polyline decoding, elevation verification, and bag upsert logic.
