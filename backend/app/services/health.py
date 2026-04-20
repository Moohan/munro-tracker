from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session


def check_health(db: Session, redis_client: Redis) -> dict[str, object]:
    database_ok = db.execute(text("SELECT 1")).scalar_one() == 1
    postgis_version = db.execute(text("SELECT PostGIS_Full_Version()")).scalar_one()
    redis_ok = redis_client.ping()

    return {
        "status": "ok" if database_ok and redis_ok else "degraded",
        "database": "ok" if database_ok else "down",
        "redis": "ok" if redis_ok else "down",
        "postgis": postgis_version,
    }
