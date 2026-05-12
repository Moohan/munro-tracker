from pydantic import BaseModel, ConfigDict, field_serializer
from decimal import Decimal

class MunroSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    height_metres: Decimal
    latitude: float
    longitude: float
    is_bagged: bool = False

    @field_serializer('height_metres')
    def serialize_height(self, value: Decimal) -> float:
        return float(value)
