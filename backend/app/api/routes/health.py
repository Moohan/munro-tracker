from fastapi import APIRouter, Depends
from redis import Redis
from sqlalchemy.orm import Session

from app.infrastructure.redis_client import get_redis
from app.infrastructure.session import get_db
from app.services.health import check_health

router = APIRouter()


@router.get("")
def healthcheck(
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> dict[str, object]:
    return check_health(db, redis_client)
