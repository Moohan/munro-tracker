import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import func, select, exists, literal
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Munro, UserBag
from app.schemas.munro import MunroSummary

router = APIRouter()

# Demo User ID used for presentation/preview
DEMO_USER_ID = uuid.UUID('00000000-0000-0000-0000-000000000001')

@router.get("", response_model=list[MunroSummary])
def list_munros(
    # NOTE: In a production environment, user_id should be derived from the
    # authenticated context (e.g., JWT). For this demo, we only allow
    # the public to view the Demo User's bagging status.
    user_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[MunroSummary]:
    # Defensive check: Only allow the Demo User ID to be queried via this public endpoint
    if user_id and user_id != DEMO_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to private user data is restricted. Only Demo Profile is publicly accessible."
        )

    # Check if each munro is bagged by the user
    is_bagged_subquery = exists().where(
        (UserBag.munro_id == Munro.id) & (UserBag.user_id == user_id)
    ) if user_id else literal(False)

    query = (
        select(
            Munro.id,
            Munro.name,
            Munro.height_metres,
            func.ST_Y(Munro.geom).label("latitude"),
            func.ST_X(Munro.geom).label("longitude"),
            is_bagged_subquery.label("is_bagged"),
        )
        .where(Munro.is_munro_top.is_(False))
        .order_by(Munro.height_metres.desc(), Munro.name.asc())
        .limit(limit)
    )
    rows = db.execute(query).all()
    return [MunroSummary.model_validate(row._mapping) for row in rows]
