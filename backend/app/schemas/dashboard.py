from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_serializer


class DashboardResponse(BaseModel):
    total_munros: int
    bagged_munros: int
    completion_percentage: Decimal
    total_ascent_metres: Decimal
    last_bagged_at: datetime | None = None

    @field_serializer("completion_percentage", "total_ascent_metres")
    def serialize_decimal(self, value: Decimal) -> float:
        return float(value)
