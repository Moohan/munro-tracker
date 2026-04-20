
# AGENTS.md


- Spatial Priority: Always use ST_DWithin with a 100m threshold for "bagging" logic. Use SRID 4326 for coordinates but cast to geography for accurate metric distance calculations.

- Data Integrity: Seed Munro data from the Database of British and Irish Hills (DoBIH) CSV. Ensure "Alternative Names" and "Munro Top" exclusions are handled correctly.

- OAuth Safety: Implement Strava Refresh Token logic immediately. Never store Access Tokens without their corresponding Refresh Token and Expiry timestamp.

- Formatting: Use British English for UI text. All elevations and distances MUST be metric (Metres/Kilometres).

- Architecture: Keep the FastAPI backend stateless; use Celery/Redis for the heavy GPX polyline decoding and spatial intersection tasks.
