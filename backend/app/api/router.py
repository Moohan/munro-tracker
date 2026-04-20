from fastapi import APIRouter

from app.api.routes import health, munros, strava

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(munros.router, prefix="/munros", tags=["munros"])
api_router.include_router(strava.router, prefix="/strava", tags=["strava"])
