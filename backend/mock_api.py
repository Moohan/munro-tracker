import uuid
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class MunroSummary(BaseModel):
    id: int
    name: str
    height_metres: float
    latitude: float
    longitude: float
    is_bagged: bool

@app.get("/api/v1/munros")
def list_munros(user_id: uuid.UUID | None = Query(None), limit: int = 300):
    return [
        {"id": 1, "name": "Ben Nevis", "height_metres": 1345.0, "latitude": 56.7968, "longitude": -5.0035, "is_bagged": True},
        {"id": 2, "name": "Ben Macdui", "height_metres": 1309.0, "latitude": 57.0704, "longitude": -3.6691, "is_bagged": False},
        {"id": 3, "name": "Braeriach", "height_metres": 1296.0, "latitude": 57.0781, "longitude": -3.7283, "is_bagged": False},
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
