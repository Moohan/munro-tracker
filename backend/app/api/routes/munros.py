import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import BigInteger, DateTime, and_, case, func, literal, select
from sqlalchemy.orm import Session

from app.infrastructure.runtime_schema import ensure_repeat_bag_schema
from app.infrastructure.session import get_db
from app.models import Munro, StravaActivity, UserBag, UserBagActivity
from app.schemas.munro import MunroSummary

router = APIRouter()


@router.get("", response_model=list[MunroSummary])
def list_munros(
    # NOTE: In a production environment, user_id should be derived from the
    # authenticated context (e.g., JWT) to prevent unauthorized access
    # to other users' bagging data.
    user_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[MunroSummary]:
    ensure_repeat_bag_schema()

    bag_details_subquery = None
    bag_count_subquery = None
    if user_id:
        bag_details_subquery = (
            select(
                UserBag.munro_id.label("munro_id"),
                UserBag.bagged_at.label("bagged_at"),
                UserBag.source_activity_id.label("source_activity_id"),
            )
            .where(UserBag.user_id == user_id)
            .subquery()
        )
        bag_count_subquery = (
            select(
                UserBagActivity.munro_id.label("munro_id"),
                func.count().label("bag_count"),
            )
            .where(UserBagActivity.user_id == user_id)
            .group_by(UserBagActivity.munro_id)
            .subquery()
        )

    query = (
        select(
            Munro.id,
            Munro.name,
            Munro.height_metres,
            func.ST_Y(Munro.geom).label("latitude"),
            func.ST_X(Munro.geom).label("longitude"),
            (
                bag_details_subquery.c.munro_id.is_not(None)
                if bag_details_subquery is not None
                else literal(False)
            ).label("is_bagged"),
            (
                bag_details_subquery.c.bagged_at
                if bag_details_subquery is not None
                else literal(None, type_=DateTime(timezone=True))
            ).label("bagged_at"),
            (
                bag_details_subquery.c.source_activity_id
                if bag_details_subquery is not None
                else literal(None, type_=BigInteger)
            ).label("source_activity_id"),
            (
                func.coalesce(
                    bag_count_subquery.c.bag_count,
                    case(
                        (bag_details_subquery.c.munro_id.is_not(None), 1),
                        else_=0,
                    ),
                )
                if bag_count_subquery is not None and bag_details_subquery is not None
                else literal(0)
            ).label("bag_count"),
        )
        .where(Munro.is_munro_top.is_(False))
        .order_by(Munro.height_metres.desc(), Munro.name.asc())
        .limit(limit)
    )
    if bag_details_subquery is not None:
        query = query.outerjoin(
            bag_details_subquery,
            bag_details_subquery.c.munro_id == Munro.id,
        )
    if bag_count_subquery is not None:
        query = query.outerjoin(
            bag_count_subquery,
            bag_count_subquery.c.munro_id == Munro.id,
        )

    rows = db.execute(query).all()
    if not user_id:
        return [MunroSummary.model_validate(row._mapping) for row in rows]

    munro_payloads: list[dict[str, object]] = []
    bagged_munro_ids: list[int] = []
    munro_lookup: dict[int, dict[str, object]] = {}

    for row in rows:
        munro_payload = dict(row._mapping)
        munro_payload["bag_count"] = int(munro_payload.get("bag_count") or 0)
        munro_payload["bag_activities"] = []
        munro_payloads.append(munro_payload)
        munro_lookup[int(munro_payload["id"])] = munro_payload
        if bool(munro_payload.get("is_bagged")):
            bagged_munro_ids.append(int(munro_payload["id"]))

    bag_activity_samples = _list_featured_bag_activities(
        db=db,
        user_id=user_id,
        munro_payloads=munro_payloads,
        bagged_munro_ids=bagged_munro_ids,
    )
    for munro_id, activities in bag_activity_samples.items():
        munro_lookup[munro_id]["bag_activities"] = activities

    return [MunroSummary.model_validate(payload) for payload in munro_payloads]


def _list_featured_bag_activities(
    *,
    db: Session,
    user_id: uuid.UUID,
    munro_payloads: list[dict[str, object]],
    bagged_munro_ids: list[int],
) -> dict[int, list[dict[str, object]]]:
    if not bagged_munro_ids:
        return {}

    activity_rows = db.execute(
        select(
            UserBagActivity.munro_id,
            UserBagActivity.source_activity_id,
            StravaActivity.name.label("name"),
            func.coalesce(
                StravaActivity.started_at,
                UserBagActivity.bagged_at,
            ).label("activity_date"),
        )
        .outerjoin(
            StravaActivity,
            and_(
                StravaActivity.user_id == UserBagActivity.user_id,
                StravaActivity.strava_activity_id == UserBagActivity.source_activity_id,
            ),
        )
        .where(
            UserBagActivity.user_id == user_id,
            UserBagActivity.munro_id.in_(bagged_munro_ids),
        )
        .order_by(
            UserBagActivity.munro_id.asc(),
            UserBagActivity.bagged_at.asc(),
            UserBagActivity.source_activity_id.asc(),
        )
    ).all()

    grouped_rows: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in activity_rows:
        grouped_rows[int(row.munro_id)].append(
            {
                "source_activity_id": int(row.source_activity_id),
                "name": row.name,
                "activity_date": row.activity_date,
            }
        )

    featured_activities: dict[int, list[dict[str, object]]] = {}
    for munro_id, rows in grouped_rows.items():
        featured_activities[munro_id] = _select_featured_activity_rows(rows)

    fallback_munros = [
        payload
        for payload in munro_payloads
        if bool(payload.get("is_bagged"))
        and int(payload["id"]) not in featured_activities
        and payload.get("source_activity_id") is not None
    ]
    if not fallback_munros:
        return featured_activities

    fallback_activity_ids = [
        int(payload["source_activity_id"])
        for payload in fallback_munros
        if payload.get("source_activity_id") is not None
    ]
    fallback_activity_rows = db.execute(
        select(
            StravaActivity.strava_activity_id,
            StravaActivity.name,
            StravaActivity.started_at,
        ).where(
            StravaActivity.user_id == user_id,
            StravaActivity.strava_activity_id.in_(fallback_activity_ids),
        )
    ).all()
    fallback_lookup = {
        int(row.strava_activity_id): {
            "name": row.name,
            "activity_date": row.started_at,
        }
        for row in fallback_activity_rows
    }

    for payload in fallback_munros:
        source_activity_id = int(payload["source_activity_id"])
        fallback_activity = fallback_lookup.get(source_activity_id, {})
        activity_date = fallback_activity.get("activity_date") or payload.get("bagged_at")
        if activity_date is None:
            continue

        featured_activities[int(payload["id"])] = [
            {
                "source_activity_id": source_activity_id,
                "name": fallback_activity.get("name"),
                "activity_date": activity_date,
            }
        ]

    return featured_activities


def _select_featured_activity_rows(
    activity_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not activity_rows:
        return []

    selected_rows: list[dict[str, object]] = []
    seen_activity_ids: set[int] = set()

    def add_activity_row(activity_row: dict[str, object]) -> None:
        activity_id = int(activity_row["source_activity_id"])
        if activity_id in seen_activity_ids:
            return
        seen_activity_ids.add(activity_id)
        selected_rows.append(activity_row)

    add_activity_row(activity_rows[0])
    for activity_row in activity_rows[-2:]:
        add_activity_row(activity_row)

    return selected_rows
