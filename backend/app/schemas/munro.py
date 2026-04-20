from pydantic import BaseModel


class MunroSummary(BaseModel):
    id: int
    name: str
    height_metres: float
    latitude: float
    longitude: float
