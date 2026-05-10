import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, exists, literal
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Munro, UserBag
from app.schemas.munro import MunroSummary

router = APIRouter()


@router.get("", response_model=list[MunroSummary])
def list_munros(
    user_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[MunroSummary]:
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
