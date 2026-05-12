import uuid
from decimal import Decimal

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models import Munro, StravaActivity, UserBag


def build_dashboard_summary(
    db: Session,
    user_id: uuid.UUID,
) -> dict[str, Decimal | int | object | None]:
    total_munros = db.execute(
        select(func.count(Munro.id)).where(Munro.is_munro_top.is_(False))
    ).scalar_one()
    bagged_munros = db.execute(
        select(func.count(UserBag.munro_id)).where(UserBag.user_id == user_id)
    ).scalar_one()
    last_bagged_at = db.execute(
        select(func.max(UserBag.bagged_at)).where(UserBag.user_id == user_id)
    ).scalar_one()

    distinct_bagged_activities = (
        select(distinct(UserBag.source_activity_id).label("activity_id"))
        .where(UserBag.user_id == user_id, UserBag.source_activity_id.is_not(None))
        .subquery()
    )
    total_ascent_metres = db.execute(
        select(func.coalesce(func.sum(StravaActivity.total_elevation_gain_metres), 0))
        .where(
            StravaActivity.user_id == user_id,
            StravaActivity.strava_activity_id.in_(
                select(distinct_bagged_activities.c.activity_id)
            ),
        )
    ).scalar_one()

    completion_percentage = Decimal("0.0")
    if total_munros > 0:
        completion_percentage = (
            Decimal(bagged_munros) * Decimal("100.0") / Decimal(total_munros)
        ).quantize(Decimal("0.1"))

    return {
        "total_munros": int(total_munros),
        "bagged_munros": int(bagged_munros),
        "completion_percentage": completion_percentage,
        "total_ascent_metres": total_ascent_metres,
        "last_bagged_at": last_bagged_at,
    }
