import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class UserBag(Base):
    __tablename__ = "user_bags"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    munro_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("munros.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_activity_id: Mapped[int | None] = mapped_column(BigInteger)
    bagged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    matched_distance_metres: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    summit_elevation_metres: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    source: Mapped[str] = mapped_column(Text, nullable=False, default="strava")

    user = relationship("User", back_populates="user_bags")
    munro = relationship("Munro", back_populates="user_bags")
