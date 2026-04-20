# MunroBagTrack: Project Specification

1. Overview

    - MunroBagTrack is a high-performance web application designed for hill-bagging enthusiasts in Scotland. It automates the "ticking off" of munro by synchronising with a user's Strava account, performing spatial point-in-polygon or proximity analysis on GPS tracks, and providing a visual dashboard of progress.

2. Technology Stack

    - Backend: Python 3.11+ with FastAPI.

    - Database: PostgreSQL with the PostGIS extension for spatial queries.

    - Frontend: React with Tailwind CSS.

    - Mapping: Leaflet.js (chosen for its extensive plugin ecosystem and simplicity for raster/vector overlays).

    - Worker/Task Queue: Celery with Redis for asynchronous processing of GPX files.

    - Authentication: Strava OAuth 2.0.

3. Data Sources

    - Hill Data: [Database of British and Irish Hills (DoBIH)](http://www.hills-database.co.uk/downloads.html). We will use the CSV export specifically filtered for "Munros".

    - Basemaps: [Ordnance Survey Data Hub (OS Maps API)](https://osdatahub.os.uk/docs/wmts/overview). This provides the essential 1:50k and 1:25k topographical detail required for Scottish hillwalking.

    - Activity Data: [Strava API v3](https://developers.strava.com/docs/reference/).

4. Key Functional Requirements

    1. Automated Synchronisation: Upon user login via Strava, the system fetches recent activities via webhooks.

    2. Spatial Analysis Engine:

        - Convert Strava polyline data into PostGIS LineString geometries.

        - Perform a ST_DWithin query to check if the track passed within 100 metres of a Munro summit coordinate.

        - Verify elevation data (from the GPS track) to ensure the summit was actually reached, preventing "drive-by" baggings.

    3. Personal Dashboard:

        - Display a list of "Bagged" vs "To-Do" Munros.

        - Calculate statistics: percentage complete, total ascent, and date of last bag.

    4. Interactive Map:

        - Custom Leaflet markers (e.g., green for climbed, red for remaining).

        - Heatmap of total hiking activity across Scotland.

5. Implementation Roadmap

    - Phase 1: Set up PostgreSQL/PostGIS and seed the database with DoBIH Munro data.

    - Phase 2: Build the FastAPI skeleton and implement Strava OAuth login.

    - Phase 3: Create the background worker logic to parse polylines and perform spatial joins.

    - Phase 4: Develop the React frontend with an OS-integrated Leaflet map.

6. Reference Material

    - [FastAPI Documentation](https://fastapi.tiangolo.com/)

    - [PostGIS Spatial Functions Reference](https://postgis.net/docs/reference.html)

    - [Strava API Developer Guide](https://developers.strava.com/docs/)

    - [Leaflet.js Documentation](https://leafletjs.com/reference-1.7.1.html)
