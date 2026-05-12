import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class StravaActivity(Base):
    __tablename__ = "strava_activities"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    strava_activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    sport_type: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_metres: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_elevation_gain_metres: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    highest_point_metres: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    summary_polyline: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    processing_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    user = relationship("User", back_populates="strava_activities")
