import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.infrastructure.runtime_schema import ensure_strava_sync_metadata_schema
from app.infrastructure.session import get_db
from app.models import User
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import build_dashboard_summary

router = APIRouter()


@router.get("/{user_id}/dashboard", response_model=DashboardResponse)
def get_user_dashboard(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DashboardResponse:
    ensure_strava_sync_metadata_schema(db)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return DashboardResponse(**build_dashboard_summary(db, user.id))
