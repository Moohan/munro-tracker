from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Munro(Base):
    __tablename__ = "munros"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dobih_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    alternative_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
    )
    hill_category: Mapped[str] = mapped_column(Text, nullable=False, default="MUN")
    is_munro_top: Mapped[bool] = mapped_column(nullable=False, default=False)
    height_metres: Mapped[Decimal] = mapped_column(Numeric(6, 1), nullable=False)
    prominence_metres: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    area: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=False)

    user_bag_activities = relationship("UserBagActivity", back_populates="munro")
    user_bags = relationship("UserBag", back_populates="munro")
