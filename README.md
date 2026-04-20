# MunroStream Local Stack

This repository now includes a container-first development skeleton for the MunroStream specification:

- PostgreSQL 16 with PostGIS for Munro storage and spatial querying
- Redis for Celery broker/result backend
- FastAPI for the stateless API surface
- Celery worker stub for heavy Strava polyline and spatial analysis
- React + Vite + Tailwind CSS for the client application

## Architectural Review

The provided spec is strong on product intent and core technology choices, but it needed a few implementation-level decisions to become buildable:

- Bagging logic is normalised around `ST_DWithin` with a fixed `100` metre threshold, using `SRID 4326` geometries cast to geography for metric accuracy.
- The API is kept stateless; polyline decoding and summit matching are reserved for a Celery worker backed by Redis.
- OAuth persistence needs token safety constraints from day one. The schema therefore includes a `users` table with refresh token and expiry metadata, avoiding unsafe access-token-only storage.
- DoBIH ingestion is expected to filter out `Munro Top` rows before persistence while still preserving `alternative_names` for matching and presentation.

## Quick Start

1. Copy `.env.example` to `.env` if you want to override defaults.
2. Start the stack:

```bash
docker compose up --build
```

3. Open:

- API docs: `http://localhost:8000/docs`
- Frontend: `http://localhost:5173`

## Layout

- `infra/db/init/001_init.sql`: PostGIS extensions and initial schema
- `backend/`: FastAPI and Celery skeleton
- `frontend/`: Vite React skeleton with Tailwind setup
- `docs/architecture-review.md`: implementation notes and next-phase recommendations

## Notes

- Docker was not available in this execution environment, so the compose stack could not be started here.
- The database initialisation script creates the required `munros` geometry table and `user_bags` join table.
- The current worker task is intentionally a stub, but its SQL template already enforces the 100 m `ST_DWithin` rule for future bagging work.
