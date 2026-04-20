from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Munro
from app.schemas.munro import MunroSummary

router = APIRouter()


@router.get("", response_model=list[MunroSummary])
def list_munros(
    limit: int = Query(default=25, ge=1, le=250),
    db: Session = Depends(get_db),
) -> list[MunroSummary]:
    query = (
        select(
            Munro.id,
            Munro.name,
            Munro.height_metres,
            func.ST_Y(Munro.geom).label("latitude"),
            func.ST_X(Munro.geom).label("longitude"),
        )
        .where(Munro.is_munro_top.is_(False))
        .order_by(Munro.height_metres.desc(), Munro.name.asc())
        .limit(limit)
    )
    rows = db.execute(query).all()
    return [MunroSummary.model_validate(row._mapping) for row in rows]
