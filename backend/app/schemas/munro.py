from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_serializer


class MunroBagActivitySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_activity_id: int
    name: str | None = None
    activity_date: datetime

class MunroSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    height_metres: Decimal
    latitude: float
    longitude: float
    is_bagged: bool = False
    bagged_at: datetime | None = None
    source_activity_id: int | None = None
    bag_count: int = 0
    bag_activities: list[MunroBagActivitySummary] = Field(default_factory=list)

    @field_serializer('height_metres')
    def serialize_height(self, value: Decimal) -> float:
        return float(value)
